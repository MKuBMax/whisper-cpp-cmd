// sysref: Swift helper that captures system audio via ScreenCaptureKit.
//
// Captures process-excluded system output audio only, no video frames.
// Emits mono float32 PCM at 16kHz to stdout; logs go to stderr.
// Protocol: parent starts process with --sysref flag, sends "stop\n" on stdin to end.
// Stdout frames: [1 byte tag][4 byte LE len][payload].
//   tag 0 = raw float32 LE PCM chunk; tag 1 = UTF-8 JSON control message.
// Control messages: {"ready":true}, {"stopped":true}, {"error":"..."}.

import AVFoundation
import CoreAudio
import CoreMedia
import Foundation
import ScreenCaptureKit

@available(macOS 13.0, *)
final class SysRefCapture: NSObject, SCStreamOutput {
    private var stream: SCStream?
    private let outHandle: FileHandle
    private let err: (String) -> Void
    private var running = true
    private var loggedFormat = false

    init(outHandle: FileHandle, err: @escaping (String) -> Void) {
        self.outHandle = outHandle
        self.err = err
    }

    func run() {
        let sema = DispatchSemaphore(value: 0)
        SCShareableContent.getWithCompletionHandler { [weak self] content, error in
            guard let self = self else { sema.signal(); return }
            if let error = error {
                self.sendError("shareable content failed: \(error.localizedDescription)")
                sema.signal()
                return
            }
            guard let display = content?.displays.first else {
                self.sendError("no display for system audio capture")
                sema.signal()
                return
            }
            let filter = SCContentFilter(display: display, excludingWindows: [])
            let config = SCStreamConfiguration()
            config.capturesAudio = true
            config.sampleRate = 48000
            config.channelCount = 2
            config.excludesCurrentProcessAudio = true
            config.width = 32
            config.height = 32
            config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
            do {
                let stream = SCStream(filter: filter, configuration: config, delegate: nil)
                self.stream = stream
                try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: .global(qos: .userInitiated))
                stream.startCapture { error in
                    if let error = error {
                        self.sendError("startCapture failed: \(error.localizedDescription)")
                    } else {
                        self.sendJSON(["ready": true])
                    }
                    sema.signal()
                }
            } catch {
                self.sendError("addStreamOutput failed: \(error.localizedDescription)")
                sema.signal()
            }
        }
        sema.wait()
        // Block on stdin until parent says stop or closes pipe.
        while running {
            if let line = readLine(strippingNewline: true) {
                if line.trimmingCharacters(in: .whitespacesAndNewlines) == "stop" {
                    break
                }
            } else {
                break
            }
        }
        stopStream()
    }

    private func stopStream() {
        guard let stream = stream else {
            sendJSON(["stopped": true])
            return
        }
        let sema = DispatchSemaphore(value: 0)
        stream.stopCapture { _ in sema.signal() }
        _ = sema.wait(timeout: .now() + 3)
        sendJSON(["stopped": true])
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, CMSampleBufferIsValid(sampleBuffer) else { return }
        let pcm = convertToMono16k(sampleBuffer)
        if !pcm.isEmpty {
            writeFrame(tag: 0, payload: pcm)
        }
    }

    private func convertToMono16k(_ sampleBuffer: CMSampleBuffer) -> Data {
        guard let format = CMSampleBufferGetFormatDescription(sampleBuffer),
              let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(format)?.pointee else { return Data() }
        let inRate = Int(asbd.mSampleRate.rounded())
        guard inRate > 0 else { return Data() }
        let channels = max(1, Int(asbd.mChannelsPerFrame))
        let isFloat = (asbd.mFormatFlags & kAudioFormatFlagIsFloat) != 0
        let isNonInterleaved = (asbd.mFormatFlags & kAudioFormatFlagIsNonInterleaved) != 0
        let frameCount = Int(CMSampleBufferGetNumSamples(sampleBuffer))
        guard frameCount > 0 else { return Data() }

        // ScreenCaptureKit 把音频放在 AudioBufferList 里，CMSampleBufferGetDataBuffer
        // 经常拿到空缓冲或未填充内存，表现为“有 samples、RMS 却接近 0”。
        var sizeNeeded = 0
        var probe = AudioBufferList()
        var probeBlock: CMBlockBuffer?
        _ = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sampleBuffer,
            bufferListSizeNeededOut: &sizeNeeded,
            bufferListOut: &probe,
            bufferListSize: MemoryLayout<AudioBufferList>.size,
            blockBufferAllocator: kCFAllocatorDefault,
            blockBufferMemoryAllocator: kCFAllocatorDefault,
            flags: 0,
            blockBufferOut: &probeBlock
        )
        if sizeNeeded < MemoryLayout<AudioBufferList>.size {
            sizeNeeded = MemoryLayout<AudioBufferList>.size + MemoryLayout<AudioBuffer>.size * max(0, channels - 1)
        }

        let rawPtr = UnsafeMutableRawPointer.allocate(byteCount: sizeNeeded, alignment: MemoryLayout<Int>.alignment)
        defer { rawPtr.deallocate() }
        rawPtr.initializeMemory(as: UInt8.self, repeating: 0, count: sizeNeeded)
        let abl = rawPtr.bindMemory(to: AudioBufferList.self, capacity: 1)
        var blockBuffer: CMBlockBuffer?
        let status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sampleBuffer,
            bufferListSizeNeededOut: nil,
            bufferListOut: abl,
            bufferListSize: sizeNeeded,
            blockBufferAllocator: kCFAllocatorDefault,
            blockBufferMemoryAllocator: kCFAllocatorDefault,
            flags: kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment,
            blockBufferOut: &blockBuffer
        )
        var mono = [Float](repeating: 0, count: frameCount)
        if status == noErr {
            fillMono(from: UnsafeMutableAudioBufferListPointer(abl), into: &mono, asbd: asbd)
        } else if let fallback = fallbackMonoFromDataBuffer(sampleBuffer, asbd: asbd, frameCount: frameCount) {
            mono = fallback
        } else {
            return Data()
        }

        if !loggedFormat {
            loggedFormat = true
            var peak: Float = 0
            var acc: Float = 0
            for x in mono {
                let a = abs(x)
                if a > peak { peak = a }
                acc += x * x
            }
            let rms = sqrt(acc / Float(frameCount))
            sendJSON([
                "audio": [
                    "rate": inRate,
                    "channels": channels,
                    "float": isFloat,
                    "nonInterleaved": isNonInterleaved,
                    "ablStatus": Int(status),
                    "peak": Double(peak),
                    "rms": Double(rms),
                ] as [String: Any]
            ])
        }
        return resampleTo16k(mono, inRate: inRate)
    }

    private func fillMono(from list: UnsafeMutableAudioBufferListPointer, into mono: inout [Float], asbd: AudioStreamBasicDescription) {
        let isFloat = (asbd.mFormatFlags & kAudioFormatFlagIsFloat) != 0
        let isSignedInt = (asbd.mFormatFlags & kAudioFormatFlagIsSignedInteger) != 0
        let isNonInterleaved = (asbd.mFormatFlags & kAudioFormatFlagIsNonInterleaved) != 0
        let channels = max(1, Int(asbd.mChannelsPerFrame))
        let bps = Int(asbd.mBitsPerChannel / 8)
        let frameCount = mono.count
        guard frameCount > 0, list.count > 0, let first = list[0].mData else { return }

        // 参考只取第一声道，避免立体声反相抵消成静音。AEC 不需要双声道。
        if isNonInterleaved || list.count > 1 {
            guard isFloat, bps == 4 else { return }
            let n0 = min(frameCount, Int(list[0].mDataByteSize) / 4)
            let s0 = first.bindMemory(to: Float.self, capacity: n0)
            for i in 0..<n0 { mono[i] = s0[i] }
            return
        }

        if isFloat, bps == 4 {
            let total = Int(list[0].mDataByteSize) / 4
            let src = first.bindMemory(to: Float.self, capacity: total)
            let frames = min(frameCount, total / channels)
            for i in 0..<frames { mono[i] = src[i * channels] }
            return
        }
        if isSignedInt, bps == 2 {
            let total = Int(list[0].mDataByteSize) / 2
            let src = first.bindMemory(to: Int16.self, capacity: total)
            let frames = min(frameCount, total / channels)
            for i in 0..<frames { mono[i] = Float(src[i * channels]) / 32768.0 }
        }
    }

    private func fallbackMonoFromDataBuffer(_ sampleBuffer: CMSampleBuffer, asbd: AudioStreamBasicDescription, frameCount: Int) -> [Float]? {
        guard let block = CMSampleBufferGetDataBuffer(sampleBuffer) else { return nil }
        var length = 0
        var dataPointer: UnsafeMutablePointer<Int8>?
        let status = CMBlockBufferGetDataPointer(
            block, atOffset: 0, lengthAtOffsetOut: nil, totalLengthOut: &length, dataPointerOut: &dataPointer
        )
        guard status == kCMBlockBufferNoErr, let ptr = dataPointer, length > 0 else { return nil }
        let isFloat = (asbd.mFormatFlags & kAudioFormatFlagIsFloat) != 0
        let isSignedInt = (asbd.mFormatFlags & kAudioFormatFlagIsSignedInteger) != 0
        let bps = Int(asbd.mBitsPerChannel / 8)
        let channels = max(1, Int(asbd.mChannelsPerFrame))
        guard bps == 2 || bps == 4 else { return nil }
        let frames = min(frameCount, length / (bps * channels))
        guard frames > 0 else { return nil }
        var mono = [Float](repeating: 0, count: frames)
        let raw = UnsafeRawPointer(ptr)
        for i in 0..<frames {
            var sum: Float = 0
            for ch in 0..<channels {
                let off = (i * channels + ch) * bps
                if isFloat, bps == 4 {
                    sum += raw.load(fromByteOffset: off, as: Float.self)
                } else if isSignedInt, bps == 2 {
                    sum += Float(raw.load(fromByteOffset: off, as: Int16.self)) / 32768.0
                } else if isSignedInt, bps == 4 {
                    sum += Float(raw.load(fromByteOffset: off, as: Int32.self)) / 2147483648.0
                }
            }
            mono[i] = sum / Float(channels)
        }
        return mono
    }

    private func resampleTo16k(_ mono: [Float], inRate: Int) -> Data {
        let frameCount = mono.count
        guard frameCount > 0, inRate > 0 else { return Data() }
        if inRate == 16000 {
            return mono.withUnsafeBufferPointer { Data(buffer: $0) }
        }
        if inRate >= 16000, inRate % 16000 == 0 {
            let step = inRate / 16000
            var out = [Float]()
            out.reserveCapacity(frameCount / step)
            var i = 0
            while i + step <= frameCount {
                var acc: Float = 0
                for k in 0..<step { acc += mono[i + k] }
                out.append(acc / Float(step))
                i += step
            }
            return out.withUnsafeBufferPointer { Data(buffer: $0) }
        }
        let nOut = Int((Double(frameCount) * 16000.0 / Double(inRate)).rounded())
        guard nOut > 0 else { return Data() }
        var out = [Float](repeating: 0, count: nOut)
        for i in 0..<nOut {
            let pos = Double(i) * Double(frameCount - 1) / Double(max(1, nOut - 1))
            let lo = Int(pos)
            let hi = min(lo + 1, frameCount - 1)
            let frac = Float(pos - Double(lo))
            out[i] = mono[lo] * (1 - frac) + mono[hi] * frac
        }
        return out.withUnsafeBufferPointer { Data(buffer: $0) }
    }

    private func writeFrame(tag: UInt8, payload: Data) {
        var header = Data()
        header.append(tag)
        var len = UInt32(payload.count).littleEndian
        header.append(Data(bytes: &len, count: 4))
        outHandle.write(header)
        if !payload.isEmpty {
            outHandle.write(payload)
        }
    }

    private func sendJSON(_ obj: [String: Any]) {
        if let data = try? JSONSerialization.data(withJSONObject: obj) {
            writeFrame(tag: 1, payload: data)
        }
    }

    private func sendError(_ message: String) {
        err(message)
        sendJSON(["error": message])
    }
}
