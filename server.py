"""
BOOSTA Bin Classification Server
Flask backend - handles image uploads, JSON storage, and REST API
"""

import os
import json
import uuid
import sqlite3
from datetime import datetime, timezone
from flask import Flask, request, jsonify, send_from_directory, send_file
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

load_dotenv()

# Cloud Storage (Optional for Cloud Deployment)
try:
    import cloudinary
    import cloudinary.uploader
    HAS_CLOUDINARY = True
except ImportError:
    cloudinary = None
    HAS_CLOUDINARY = False

app = Flask(__name__, static_folder="public", static_url_path="")

# ── Config ──────────────────────────────────────────────────────────────
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
DB_FILE = os.path.join(os.path.dirname(__file__), "boosta.db")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "bmp"}
MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ── Cloudinary Config ──────────────────────────────────────────────────
# These should be set as Environment Variables on your cloud provider
if HAS_CLOUDINARY and os.environ.get("CLOUDINARY_CLOUD_NAME"):
    cloudinary.config(
        cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME"),
        api_key=os.environ.get("CLOUDINARY_API_KEY"),
        api_secret=os.environ.get("CLOUDINARY_API_SECRET"),
        secure=True
    )
    print("  [Cloud] Cloudinary configured for image storage.")
else:
    print("  [Local] Using local folder for image storage.")

# ── Helpers ──────────────────────────────────────────────────────────────
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def get_db_connection():
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS bins (
            id TEXT PRIMARY KEY,
            bin_id TEXT,
            aisle TEXT,
            reported_by TEXT,
            description TEXT,
            urgency INTEGER,
            boosta_categories TEXT,
            image_path TEXT,
            image_url TEXT,
            timestamp TEXT,
            status TEXT
        )
    ''')
    conn.commit()
    conn.close()

# Initialize DB on startup
init_db()


# ── Routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


@app.route("/api/bins", methods=["GET"])
def get_bins():
    """Return all submissions. Optionally filter by urgency."""
    urgency = request.args.get("urgency")
    category = request.args.get("category")

    conn = get_db_connection()
    query = "SELECT * FROM bins"
    params = []
    
    conditions = []
    if urgency:
        conditions.append("urgency = ?")
        params.append(int(urgency))
    if category:
        conditions.append("boosta_categories LIKE ?")
        params.append(f'%"{category}"%')
        
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
        
    query += " ORDER BY urgency DESC, timestamp DESC"
    
    rows = conn.execute(query, params).fetchall()
    conn.close()

    data = []
    for row in rows:
        b = dict(row)
        try:
            b["boosta_categories"] = json.loads(b["boosta_categories"])
        except:
            b["boosta_categories"] = []
        data.append(b)

    return jsonify({"success": True, "count": len(data), "bins": data})


@app.route("/api/submit", methods=["POST"])
def submit_bin():
    """Accept a bin report: image + metadata."""
    # Parse form fields
    bin_id = request.form.get("bin_id", "").strip()
    description = request.form.get("description", "").strip()
    urgency_raw = request.form.get("urgency", "1")
    boosta_raw = request.form.get("boosta_categories", "[]")
    aisle = request.form.get("aisle", "").strip()
    reported_by = request.form.get("reported_by", "").strip()

    try:
        urgency = int(urgency_raw)
        urgency = max(1, min(5, urgency))
    except ValueError:
        urgency = 1

    try:
        boosta_categories = json.loads(boosta_raw)
        if not isinstance(boosta_categories, list):
            boosta_categories = []
    except (json.JSONDecodeError, TypeError):
        boosta_categories = []

    # Handle image upload
    image_path = None
    image_url = None
    if "image" in request.files:
        file = request.files["image"]
        if file and file.filename and allowed_file(file.filename):
            # Option 1: Cloudinary (If keys are provided)
            if HAS_CLOUDINARY and os.environ.get("CLOUDINARY_CLOUD_NAME"):
                try:
                    upload_result = cloudinary.uploader.upload(file)
                    image_url = upload_result.get("secure_url")
                except Exception as e:
                    print(f"  [Error] Cloudinary upload failed: {e}")
            
            # Option 2: Local Storage (Fallback)
            if not image_url:
                ext = file.filename.rsplit(".", 1)[1].lower()
                unique_name = f"{uuid.uuid4().hex}.{ext}"
                save_path = os.path.join(UPLOAD_FOLDER, unique_name)
                file.save(save_path)
                image_path = unique_name
                image_url = f"/uploads/{unique_name}"

    # Build record
    record = {
        "id": str(uuid.uuid4()),
        "bin_id": bin_id,
        "aisle": aisle,
        "reported_by": reported_by,
        "description": description,
        "urgency": urgency,
        "boosta_categories": boosta_categories,
        "image_path": image_path,
        "image_url": image_url,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "open",  # open | resolved
    }

    conn = get_db_connection()
    conn.execute('''
        INSERT INTO bins (id, bin_id, aisle, reported_by, description, urgency, boosta_categories, image_path, image_url, timestamp, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        record["id"], record["bin_id"], record["aisle"], record["reported_by"],
        record["description"], record["urgency"], json.dumps(record["boosta_categories"]),
        record["image_path"], record["image_url"], record["timestamp"], record["status"]
    ))
    conn.commit()
    conn.close()

    return jsonify({"success": True, "record": record}), 201


@app.route("/api/bins/<bin_record_id>", methods=["DELETE"])
def delete_bin(bin_record_id):
    """Delete a submission by its UUID."""
    conn = get_db_connection()
    row = conn.execute("SELECT image_path FROM bins WHERE id = ?", (bin_record_id,)).fetchone()

    if not row:
        conn.close()
        return jsonify({"success": False, "error": "Record not found"}), 404

    # Delete image file if exists
    if row["image_path"]:
        img_file = os.path.join(UPLOAD_FOLDER, row["image_path"])
        if os.path.exists(img_file):
            os.remove(img_file)

    conn.execute("DELETE FROM bins WHERE id = ?", (bin_record_id,))
    conn.commit()
    conn.close()

    return jsonify({"success": True, "deleted": bin_record_id})


@app.route("/api/bins/<bin_record_id>/resolve", methods=["PATCH"])
def resolve_bin(bin_record_id):
    """Toggle a bin's status between open and resolved."""
    conn = get_db_connection()
    row = conn.execute("SELECT status FROM bins WHERE id = ?", (bin_record_id,)).fetchone()

    if not row:
        conn.close()
        return jsonify({"success": False, "error": "Record not found"}), 404

    new_status = "resolved" if row["status"] == "open" else "open"
    conn.execute("UPDATE bins SET status = ? WHERE id = ?", (new_status, bin_record_id))
    conn.commit()

    # Fetch updated record
    updated_row = conn.execute("SELECT * FROM bins WHERE id = ?", (bin_record_id,)).fetchone()
    conn.close()

    record = dict(updated_row)
    try:
        record["boosta_categories"] = json.loads(record["boosta_categories"])
    except:
        record["boosta_categories"] = []

    return jsonify({"success": True, "record": record})


@app.route("/api/stats", methods=["GET"])
def get_stats():
    """Return summary statistics."""
    conn = get_db_connection()
    
    total = conn.execute("SELECT COUNT(*) FROM bins").fetchone()[0]
    open_count = conn.execute("SELECT COUNT(*) FROM bins WHERE status = 'open'").fetchone()[0]
    resolved_count = conn.execute("SELECT COUNT(*) FROM bins WHERE status = 'resolved'").fetchone()[0]
    
    urgency_rows = conn.execute("SELECT urgency, COUNT(*) FROM bins GROUP BY urgency").fetchall()
    urgency_counts = {str(i): 0 for i in range(1, 6)}
    for r in urgency_rows:
        urgency_counts[str(r[0])] = r[1]
        
    category_counts = {"B": 0, "O1": 0, "O2": 0, "S": 0, "T": 0, "A": 0}
    boosta_rows = conn.execute("SELECT boosta_categories FROM bins").fetchall()
    for r in boosta_rows:
        try:
            cats = json.loads(r[0])
            for cat in cats:
                if cat in category_counts:
                    category_counts[cat] += 1
        except:
            pass
            
    conn.close()

    return jsonify({
        "success": True,
        "total": total,
        "open": open_count,
        "resolved": resolved_count,
        "urgency_counts": urgency_counts,
        "category_counts": category_counts,
    })


if __name__ == "__main__":
    # Get port from environment (Render/Railway/Heroku set this automatically)
    port = int(os.environ.get("PORT", 5000))
    
    print("=" * 55)
    print("  BOOSTA Bin Classification Server")
    print(f"  Running on port: {port}")
    print("=" * 55)
    
    try:
        from waitress import serve
        print("  [Waitress] Running with PRODUCTION server")
        print("  Handling concurrent uploads safely.")
        serve(app, host="0.0.0.0", port=port)
    except ImportError:
        print("  [WARNING] Waitress is not installed. Running with development server.")
        app.run(debug=True, port=port, host="0.0.0.0")
