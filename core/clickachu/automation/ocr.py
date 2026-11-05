"""Offline Windows OCR and window-scoped capture for text-only local models."""

import asyncio


def capture(window, hidden=()):
    import ctypes

    import win32gui
    import win32ui
    from PIL import Image, ImageDraw

    rect = window.rectangle()
    width, height = rect.right - rect.left, rect.bottom - rect.top
    dc = win32gui.GetWindowDC(window.handle)
    source = win32ui.CreateDCFromHandle(dc)
    target = source.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    try:
        bitmap.CreateCompatibleBitmap(source, width, height)
        target.SelectObject(bitmap)
        if not ctypes.windll.user32.PrintWindow(window.handle, target.GetSafeHdc(), 2):
            raise RuntimeError("The app did not provide a window image.")
        image = Image.frombuffer(
            "RGB", (width, height), bitmap.GetBitmapBits(True), "raw", "BGRX", 0, 1
        )
        if image.convert("L").getextrema()[1] < 2:
            raise RuntimeError("The app returned an empty window image.")
        draw = ImageDraw.Draw(image)
        for left, top, right, bottom in hidden:
            draw.rectangle(
                (left - rect.left, top - rect.top, right - rect.left, bottom - rect.top),
                fill="black",
            )
        return image
    finally:
        if bitmap.GetHandle():
            win32gui.DeleteObject(bitmap.GetHandle())
        target.DeleteDC()
        source.DeleteDC()
        win32gui.ReleaseDC(window.handle, dc)


async def recognize(image):
    from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine

    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise RuntimeError("Windows OCR has no installed language recognizer.")
    scale = min(1.0, OcrEngine.max_image_dimension / max(image.size))
    if scale < 1:
        image = image.resize((int(image.width * scale), int(image.height * scale)))
    bitmap = SoftwareBitmap.create_copy_from_buffer(
        image.convert("RGBA").tobytes("raw", "BGRA"),
        BitmapPixelFormat.BGRA8,
        image.width,
        image.height,
        BitmapAlphaMode.IGNORE,
    )
    try:
        result = await engine.recognize_async(bitmap)
        entries = []
        for line in result.lines:
            for word in line.words:
                box = word.bounding_rect
                entries.append(
                    (
                        word.text,
                        (
                            box.x / scale,
                            box.y / scale,
                            (box.x + box.width) / scale,
                            (box.y + box.height) / scale,
                        ),
                    )
                )
        return entries
    finally:
        bitmap.close()


def words(window, hidden=()):
    return asyncio.run(recognize(capture(window, hidden)))


def same_target(expected, current):
    text, box = expected
    return any(
        text == value and max(abs(a - b) for a, b in zip(box, bounds, strict=True)) < 4
        for value, bounds in current
    )
