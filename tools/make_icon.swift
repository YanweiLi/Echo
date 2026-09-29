// Echo 图标生成器：用 CoreGraphics 画出「回声波纹」图标
// 用法：make_icon <输出目录.iconset>

import AppKit
import CoreGraphics

let outDir = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "./Echo.iconset"
try? FileManager.default.createDirectory(atPath: outDir, withIntermediateDirectories: true)

func render(_ px: Int) -> Data? {
    let size = CGFloat(px)
    guard let ctx = CGContext(data: nil,
                              width: px, height: px,
                              bitsPerComponent: 8, bytesPerRow: 0,
                              space: CGColorSpaceCreateDeviceRGB(),
                              bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
        return nil
    }

    // 圆角矩形背景 + 渐变（经济学家红 → 深红）
    let rect = CGRect(x: 0, y: 0, width: size, height: size)
    let radius = size * 0.2237
    let shape = CGPath(roundedRect: rect, cornerWidth: radius, cornerHeight: radius, transform: nil)
    ctx.saveGState()
    ctx.addPath(shape)
    ctx.clip()
    let colors = [
        CGColor(red: 0.87, green: 0.30, blue: 0.24, alpha: 1.0),
        CGColor(red: 0.52, green: 0.08, blue: 0.10, alpha: 1.0),
    ] as CFArray
    if let gradient = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(),
                                 colors: colors, locations: [0.0, 1.0]) {
        ctx.drawLinearGradient(gradient,
                               start: CGPoint(x: 0, y: size),
                               end: CGPoint(x: size, y: 0),
                               options: [])
    }
    ctx.restoreGState()

    // 声源圆点
    let origin = CGPoint(x: size * 0.31, y: size * 0.5)
    let dot = size * 0.125
    ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
    ctx.fillEllipse(in: CGRect(x: origin.x - dot / 2, y: origin.y - dot / 2,
                               width: dot, height: dot))

    // 三道回声波纹
    ctx.setStrokeColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
    ctx.setLineCap(.round)
    ctx.setLineWidth(size * 0.057)
    for r in [size * 0.155, size * 0.275, size * 0.395] {
        ctx.addArc(center: origin, radius: r,
                   startAngle: -0.70, endAngle: 0.70, clockwise: false)
        ctx.strokePath()
    }

    guard let image = ctx.makeImage() else { return nil }
    let rep = NSBitmapImageRep(cgImage: image)
    rep.size = NSSize(width: px, height: px)
    return rep.representation(using: .png, properties: [:])
}

let specs: [(String, Int)] = [
    ("icon_16x16.png", 16), ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32), ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128), ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256), ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512), ("icon_512x512@2x.png", 1024),
]

var written = 0
for (name, px) in specs {
    if let data = render(px) {
        let url = URL(fileURLWithPath: outDir).appendingPathComponent(name)
        if (try? data.write(to: url)) != nil { written += 1 }
    }
}
print("生成 \(written)/\(specs.count) 个图标到 \(outDir)")
