#!/usr/bin/env swift

import AppKit
import Foundation
import Vision

struct OCRBlock: Encodable {
    let image: String
    let image_index: Int
    let text: String
    let confidence: Float
    let x: Double
    let y: Double
    let width: Double
    let height: Double
}

struct OCRPayload: Encodable {
    let blocks: [OCRBlock]
}

func recognize(path: String, imageIndex: Int) throws -> [OCRBlock] {
    let url = URL(fileURLWithPath: path)
    guard let image = NSImage(contentsOf: url) else {
        throw NSError(domain: "VisionOCR", code: 1, userInfo: [NSLocalizedDescriptionKey: "Cannot open image: \(path)"])
    }
    guard let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        throw NSError(domain: "VisionOCR", code: 2, userInfo: [NSLocalizedDescriptionKey: "Cannot convert image: \(path)"])
    }

    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    request.recognitionLanguages = ["zh-Hans", "en-US"]

    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    try handler.perform([request])

    let observations = request.results ?? []
    return observations.compactMap { observation in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let box = observation.boundingBox
        return OCRBlock(
            image: url.lastPathComponent,
            image_index: imageIndex,
            text: candidate.string,
            confidence: candidate.confidence,
            x: box.origin.x,
            y: box.origin.y,
            width: box.size.width,
            height: box.size.height
        )
    }
}

let args = Array(CommandLine.arguments.dropFirst())
if args.isEmpty {
    fputs("Usage: vision_ocr.swift <image> [image...]\n", stderr)
    exit(2)
}

do {
    var blocks: [OCRBlock] = []
    for (offset, path) in args.enumerated() {
        blocks.append(contentsOf: try recognize(path: path, imageIndex: offset + 1))
    }
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
    let data = try encoder.encode(OCRPayload(blocks: blocks))
    FileHandle.standardOutput.write(data)
} catch {
    fputs("\(error.localizedDescription)\n", stderr)
    exit(1)
}
