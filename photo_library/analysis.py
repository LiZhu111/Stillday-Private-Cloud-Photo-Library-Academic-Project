"""Deterministic image analysis. Scores are review hints, never delete rules."""
import hashlib
import io
from datetime import datetime
from PIL import Image, ImageFilter, ImageOps, ImageStat

Image.MAX_IMAGE_PIXELS = 40_000_000


def analyse(raw):
    with Image.open(io.BytesIO(raw)) as original:
        if original.format not in {'JPEG', 'MPO', 'PNG', 'WEBP'}:
            raise ValueError('Only JPEG, MPO, PNG and WebP images are supported')
        if original.width * original.height > Image.MAX_IMAGE_PIXELS:
            raise ValueError('Image exceeds the pixel limit')
        if getattr(original, 'n_frames', 1) != 1 and original.format != 'MPO':
            raise ValueError('Animated images are not supported')
        # MPO is a multi-picture JPEG container commonly produced by phones.
        # Use its primary frame for previews and review metrics.
        if original.format == 'MPO':
            original.seek(0)
        captured = None
        try:
            exif = original.getexif()
            date_value = exif.get_ifd(0x8769).get(36867) or exif.get(36867)
            if date_value:
                captured = datetime.strptime(str(date_value), '%Y:%m:%d %H:%M:%S').date().isoformat()
        except (ValueError, TypeError, KeyError, AttributeError):
            pass
        picture = ImageOps.exif_transpose(original).convert('RGB')
        width, height = picture.size
        gray = picture.convert('L')
        gray.thumbnail((512, 512))
        edge = gray.filter(ImageFilter.FIND_EDGES)
        if edge.width > 2 and edge.height > 2:
            edge = edge.crop((1, 1, edge.width-1, edge.height-1))
        sharpness = round(ImageStat.Stat(edge).mean[0], 2)
        brightness = round(ImageStat.Stat(gray).mean[0], 2)
        small = picture.convert('L').resize((9, 8), Image.Resampling.LANCZOS)
        values = list(small.get_flattened_data())
        bits = [values[y*9+x] > values[y*9+x+1] for y in range(8) for x in range(8)]
        fingerprint = sum(int(bit) << index for index, bit in enumerate(bits))
        warnings = []
        if sharpness < 8:
            warnings.append('Low edge detail; review for blur or an intentionally simple scene')
        if brightness < 35 or brightness > 225:
            warnings.append('Very dark or bright image; review exposure')
        if max(width, height) < 1280:
            warnings.append('Limited resolution for large displays')
        variants = []
        for size in (320, 1280, 2560):
            copy = picture.copy()
            copy.thumbnail((size, size), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            copy.save(output, 'JPEG', quality=88, optimize=True)
            variants.append((str(size), output.getvalue(), copy.width, copy.height))
    return {'sha256': hashlib.sha256(raw).hexdigest(), 'dhash': f'{fingerprint:016x}',
            'width': width, 'height': height, 'capturedDate': captured,
            'orientation': 'landscape' if width > height else 'portrait' if height > width else 'square',
            'sharpness': sharpness, 'brightness': brightness, 'qualityWarnings': warnings}, variants


def distance(left, right):
    return (int(left, 16) ^ int(right, 16)).bit_count()

