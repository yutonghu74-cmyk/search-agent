"""Small real images for delivery tests; no external files or services."""
from io import BytesIO
from PIL import Image


def image_bytes(format='JPEG'):
    output = BytesIO()
    Image.new('RGB', (8, 8), (40, 100, 160)).save(output, format=format)
    return output.getvalue()
