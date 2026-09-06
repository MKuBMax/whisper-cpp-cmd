// sysref: Swift helper that captures system audio via ScreenCaptureKit.
//
// Captures process-excluded system output audio only, no video frames.
// Emits mono float32 PCM at 16kHz to stdout; logs go to stderr.
// Protocol: parent starts process with --sysref flag, sends "stop\n" on stdin to end.
// Stdout frames: [1 byte tag][4 byte LE len][payload].
//   tag 0 = raw float32 LE PCM chunk; tag 1 = UTF-8 JSON control message.
// Control messages: {"ready":true}, {"stopped":true}, {"error":"..."}.

import AVFoundation
import Foundation
import ScreenCaptureKit

@available(macOS 13.0, *)
final class SysRefCapture: NSObject, SCStreamOutput {
    private var stream: SCStream?
    private let outHandle: FileHandle
    private let err: (String) -> Void
    private var running = true

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
        guard let block = CMSampleBufferGetDataBuffer(sampleBuffer) else { return }
        var length = 0
        var dataPointer: UnsafeMutablePointer<Int8>?
        let status = CMBlockBufferGetDataPointer(block, atOffset: 0, lengthAtOffsetOut: nil, totalLengthOut: &length, dataPointerOut: &dataPointer)
        guard status == kCMBlockBufferNoErr, let ptr = dataPointer, length > 0 else { return }
        guard let format = CMSampleBufferGetFormatDescription(sampleBuffer),
              let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(format)?.pointee else { return }
        let bytes = Data(bytes: ptr, count: length)
        let pcm = convertToMono16k(bytes: bytes, asbd: asbd)
        if !pcm.isEmpty {
            writeFrame(tag: 0, payload: pcm)
        }
    }

    private func convertToMono16k(bytes: Data, asbd: AudioStreamBasicDescription) -> Data {
        let isFloat = (asbd.mFormatFlags & kAudioFormatFlagIsFloat) != 0
        let isSignedInt = (asbd.mFormatFlags & kAudioFormatFlagIsSignedInteger) != 0
        let bps = Int(asbd.mBitsPerChannel / 8)
        let channels = max(1, Int(asbd.mChannelsPerFrame))
        let inRate = Int(asbd.mSampleRate)
        guard bps == 2 || bps == 4 else { return Data() }
        let frameCount = bytes.count / (bps * channels)
        guard frameCount > 0 else { return Data() }
        var mono = [Float](repeating: 0, count: frameCount)
        bytes.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            for i in 0..<frameCount {
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
        }
        if inRate == 16000 {
            return mono.withUnsafeBufferPointer { Data(buffer: $0) }
        }
        // Integer-ratio decimation preserves energy; else linear interpolate.
        if inRate % 16000 == 0 {
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
