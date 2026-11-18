
from flask import (
    Flask, request, jsonify, render_template,
    send_from_directory
)
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4
import wave
import io
import json

# ---------- PATHS AND CONFIGURATION ----------
BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "templates"
RECORDINGS_DIR = BASE_DIR / "recordings"
METADATA_FILE = RECORDINGS_DIR / "metadata.json"

TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(
    __name__,
    template_folder=str(TEMPLATE_DIR)
)

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_RECORDING_SECONDS = 15.5

app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES


# ---------- METADATA ----------
def load_metadata():
    if not METADATA_FILE.exists():
        return []

    try:
        records = json.loads(
            METADATA_FILE.read_text(encoding="utf-8")
        )
        return records if isinstance(records, list) else []
    except (OSError, json.JSONDecodeError):
        app.logger.exception("Could not read recording metadata")
        return []


def save_metadata(records):
    temp_file = METADATA_FILE.with_suffix(".tmp")
    temp_file.write_text(
        json.dumps(records, indent=2),
        encoding="utf-8"
    )
    temp_file.replace(METADATA_FILE)


def find_recording(recording_id):
    for item in load_metadata():
        if item.get("id") == recording_id:
            return item
    return None


# ---------- WAV VALIDATION ----------
def validate_wav(data):
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            if wav.getnchannels() != 1:
                return False, "Expected mono audio"

            if wav.getsampwidth() != 2:
                return False, "Expected 16-bit PCM audio"

            if wav.getframerate() != 16000:
                return False, "Expected a 16000 Hz sample rate"

            if wav.getcomptype() != "NONE":
                return False, "Expected uncompressed PCM WAV"

            frames = wav.getnframes()
            if frames <= 0:
                return False, "Audio is empty"

            duration = frames / wav.getframerate()

            if duration > MAX_RECORDING_SECONDS:
                return False, "Recording exceeds 15.5 seconds"

            return True, duration

    except (wave.Error, EOFError, OSError):
        return False, "Invalid or incomplete WAV file"


# ---------- WEBSITE ----------
@app.get("/")
def index():
    return render_template("index.html")


# ---------- HEALTH CHECK ----------
@app.get("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "ESP32 Audio Backend",
        "template_exists": (TEMPLATE_DIR / "index.html").is_file()
    })


# ---------- UPLOAD AUDIO FROM ESP32 ----------
@app.post("/api/upload")
def upload_recording():
    if request.mimetype != "audio/wav":
        return jsonify({
            "error": "Content-Type must be audio/wav"
        }), 415

    data = request.get_data(cache=False)

    if not data:
        return jsonify({"error": "Empty upload"}), 400

    valid, result = validate_wav(data)

    if not valid:
        return jsonify({"error": result}), 400

    recording_id = uuid4().hex
    filename = f"{recording_id}.wav"
    destination = RECORDINGS_DIR / filename

    try:
        destination.write_bytes(data)

        item = {
            "id": recording_id,
            "filename": filename,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": round(result, 2),
            "size_bytes": len(data)
        }

        records = load_metadata()
        records.insert(0, item)
        save_metadata(records)

    except OSError:
        destination.unlink(missing_ok=True)
        app.logger.exception("Failed to save recording")
        return jsonify({"error": "Could not save recording"}), 500

    app.logger.info("Saved recording %s", recording_id)

    return jsonify({
        "message": "Recording uploaded successfully",
        "recording": item
    }), 201


# ---------- LIST RECORDINGS ----------
@app.get("/api/recordings")
def list_recordings():
    return jsonify(load_metadata())


# ---------- RECORDING DETAILS ----------
@app.get("/api/recordings/<recording_id>")
def get_recording(recording_id):
    item = find_recording(recording_id)

    if item is None:
        return jsonify({"error": "Recording not found"}), 404

    return jsonify(item)


# ---------- PLAY AUDIO ----------
@app.get("/api/recordings/<recording_id>/audio")
def play_recording(recording_id):
    item = find_recording(recording_id)

    if item is None:
        return jsonify({"error": "Recording not found"}), 404

    response = send_from_directory(
        str(RECORDINGS_DIR),
        item["filename"],
        mimetype="audio/wav",
        as_attachment=False
    )
    response.headers["Cache-Control"] = "no-store"
    return response


# ---------- DOWNLOAD AUDIO ----------
@app.get("/api/recordings/<recording_id>/download")
def download_recording(recording_id):
    item = find_recording(recording_id)

    if item is None:
        return jsonify({"error": "Recording not found"}), 404

    return send_from_directory(
        str(RECORDINGS_DIR),
        item["filename"],
        mimetype="audio/wav",
        as_attachment=True,
        download_name=f"recording_{recording_id}.wav"
    )


# ---------- DELETE RECORDING ----------
@app.delete("/api/recordings/<recording_id>")
def delete_recording(recording_id):
    item = find_recording(recording_id)

    if item is None:
        return jsonify({"error": "Recording not found"}), 404

    # Remove the audio file
    audio_file = RECORDINGS_DIR / item["filename"]
    try:
        audio_file.unlink(missing_ok=True)
    except OSError:
        app.logger.exception("Failed to delete audio file for %s", recording_id)
        return jsonify({"error": "Could not delete audio file"}), 500

    # Remove from metadata
    records = [r for r in load_metadata() if r.get("id") != recording_id]
    save_metadata(records)

    app.logger.info("Deleted recording %s", recording_id)
    return jsonify({"message": "Recording deleted", "id": recording_id})


# ---------- FRIENDLY ERROR RESPONSES ----------
@app.errorhandler(413)
def file_too_large(error):
    return jsonify({"error": "Upload exceeds the size limit"}), 413


@app.errorhandler(404)
def not_found(error):
    if request.path.startswith("/api/"):
        return jsonify({"error": "API endpoint not found"}), 404
    return "Page not found", 404


if __name__ == "__main__":
    print("ESP32 Audio Backend")
    print(f"Template directory: {TEMPLATE_DIR}")
    print(f"HTML file exists: {(TEMPLATE_DIR / 'index.html').is_file()}")
    print(f"Recordings directory: {RECORDINGS_DIR}")
    print("Local website: http://127.0.0.1:5000")
    print("Network website: http://10.249.15.151:5000")

    app.run(host="0.0.0.0", port=5000, debug=False)
