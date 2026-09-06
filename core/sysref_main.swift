import Foundation

@main
struct SysRefMain {
    static func main() {
        guard CommandLine.arguments.contains("--sysref") else {
            FileHandle.standardError.write(Data("usage: sysref_capture --sysref\n".utf8))
            exit(2)
        }
        if #available(macOS 13.0, *) {
            let cap = SysRefCapture(outHandle: FileHandle.standardOutput) { msg in
                if let data = ("[sysref] " + msg + "\n").data(using: .utf8) {
                    FileHandle.standardError.write(data)
                }
            }
            cap.run()
        } else {
            FileHandle.standardError.write(Data("sysref needs macOS 13+\n".utf8))
            exit(1)
        }
    }
}
