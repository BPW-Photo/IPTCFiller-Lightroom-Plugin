import anthropic
import base64
import sys
import os
import io
import json
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS
from datetime import datetime

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

try:
    from geopy.geocoders import Nominatim
    from geopy.exc import GeocoderTimedOut
    GEOPY_AVAILABLE = True
except ImportError:
    GEOPY_AVAILABLE = False

# ============================================================
#  PASTE YOUR ANTHROPIC API KEY BETWEEN THE QUOTES BELOW
# ============================================================
API_KEY = "YOUR-API-KEY-HERE"
# ============================================================

PHOTOGRAPHER_NAME = "Rob Durston"
COPYRIGHT_NOTICE = f"©{datetime.now().year} Rob Durston Photography"

NORTHERN_IRELAND_COUNTIES = [
    "county antrim", "county down", "county armagh",
    "county tyrone", "county fermanagh", "county londonderry",
    "county derry", "antrim", "down", "armagh",
    "tyrone", "fermanagh", "londonderry", "derry"
]

def get_exif_data(img):
    exif_data = {}
    try:
        raw_exif = img._getexif()
        if raw_exif:
            for tag_id, value in raw_exif.items():
                tag = TAGS.get(tag_id, tag_id)
                exif_data[tag] = value
    except Exception:
        pass
    return exif_data

def get_gps_coords(exif_data):
    if "GPSInfo" not in exif_data:
        return None, None
    gps_info = {}
    for key, val in exif_data["GPSInfo"].items():
        tag = GPSTAGS.get(key, key)
        gps_info[tag] = val
    try:
        def to_degrees(value):
            d, m, s = value
            return float(d) + float(m) / 60 + float(s) / 3600
        lat = to_degrees(gps_info["GPSLatitude"])
        if gps_info["GPSLatitudeRef"] == "S":
            lat = -lat
        lon = to_degrees(gps_info["GPSLongitude"])
        if gps_info["GPSLongitudeRef"] == "W":
            lon = -lon
        return lat, lon
    except Exception:
        return None, None

def reverse_geocode(lat, lon):
    if not GEOPY_AVAILABLE:
        return {}
    try:
        geolocator = Nominatim(user_agent="iptc_filler_lightroom")
        location = geolocator.reverse(f"{lat}, {lon}", exactly_one=True, language="en")
        if not location:
            return {}
        addr = location.raw.get("address", {})
        county = (addr.get("county") or addr.get("state_district") or addr.get("region") or "")
        city = (addr.get("city") or addr.get("town") or addr.get("village") or addr.get("hamlet") or "")
        sublocation = (addr.get("suburb") or addr.get("neighbourhood") or addr.get("nature_reserve") or addr.get("leisure") or addr.get("amenity") or "")
        country = addr.get("country", "")

        # Use state field for Northern Ireland — more reliable than country field
        state = addr.get("state", "")
        if state == "Northern Ireland":
            country = "Northern Ireland"
        elif county.lower() in NORTHERN_IRELAND_COUNTIES:
            country = "Northern Ireland"

        return {
            "sublocation": sublocation,
            "city": city,
            "county": county,
            "country": country,
        }
    except GeocoderTimedOut:
        return {}
    except Exception:
        return {}

def build_ai_request(image_data, location):
    if location.get("city") or location.get("country"):
        location_hint = (
            f"GPS data indicates this photo was taken in "
            f"{location.get('sublocation', '')} "
            f"{location.get('city', '')} "
            f"{location.get('county', '')} "
            f"{location.get('country', '')}. "
            f"Use this for location fields."
        )
    else:
        location_hint = "No GPS data available. Try to identify location from visual clues, or leave location fields empty if uncertain."

    client = anthropic.Anthropic(api_key=API_KEY)
    message = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_data}},
            {"type": "text", "text": (
                "You are an expert photo metadata assistant for Adobe Lightroom.\n"
                "Analyse this image and return a JSON object with these exact fields:\n"
                "title, caption, keywords, category, sublocation, city, county, country\n\n"
                "- title: short professional title (max 70 chars)\n"
                "- caption: descriptive stock photography caption (max 200 chars)\n"
                "- keywords: comma-separated list of 15-30 keywords in lowercase\n"
                "- category: one of: Nature, People, Architecture, Travel, Sport, Food, Business\n"
                "- sublocation: specific place name if identifiable\n"
                "- city: city name if identifiable\n"
                "- county: county name (important for UK/Ireland e.g. County Antrim, County Down)\n"
                "- country: country name\n\n"
                f"{location_hint}\n\n"
                "Return ONLY valid JSON. No explanation, no markdown, no code blocks."
            )}
        ]}],
    )
    raw = message.content[0].text.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(raw)

def prepare_image(image_path):
    img = Image.open(image_path)
    img.thumbnail((1568, 1568), Image.LANCZOS)
    buffer = io.BytesIO()
    img.convert("RGB").save(buffer, format="JPEG", quality=85)
    buffer.seek(0)
    return base64.standard_b64encode(buffer.read()).decode("utf-8")

def analyse_image(image_path):
    img = Image.open(image_path)
    exif_data = get_exif_data(img)
    lat, lon = get_gps_coords(exif_data)
    location = {}
    if lat is not None and lon is not None:
        location = reverse_geocode(lat, lon)
    image_data = prepare_image(image_path)
    ai_data = build_ai_request(image_data, location)
    for field in ["sublocation", "city", "county", "country"]:
        if location.get(field) and not ai_data.get(field):
            ai_data[field] = location[field]
    if location.get("country"):
        ai_data["country"] = location["country"]
    ai_data["creator"] = PHOTOGRAPHER_NAME
    ai_data["copyright"] = COPYRIGHT_NOTICE
    return ai_data

def analyse_image_with_gps(image_path, lat, lon):
    location = reverse_geocode(lat, lon)
    image_data = prepare_image(image_path)
    ai_data = build_ai_request(image_data, location)
    for field in ["sublocation", "city", "county", "country"]:
        if location.get(field) and not ai_data.get(field):
            ai_data[field] = location[field]
    if location.get("country"):
        ai_data["country"] = location["country"]
    ai_data["creator"] = PHOTOGRAPHER_NAME
    ai_data["copyright"] = COPYRIGHT_NOTICE
    return ai_data

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 iptc_filler.py <path_to_image> [lat] [lon]")
        sys.exit(1)
    image_path = sys.argv[1]
    if len(sys.argv) >= 4:
        try:
            ext_lat = float(sys.argv[2])
            ext_lon = float(sys.argv[3])
            result = analyse_image_with_gps(image_path, ext_lat, ext_lon)
        except Exception:
            result = analyse_image(image_path)
    else:
        result = analyse_image(image_path)
    print(json.dumps(result, indent=2, ensure_ascii=False))
