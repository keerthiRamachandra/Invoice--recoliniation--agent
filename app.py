from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import os
import pandas as pd
from werkzeug.utils import secure_filename
import uuid
from datetime import datetime

app = Flask(__name__, static_folder="static")
CORS(app)

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {"csv", "xlsx", "xls", "json"}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def load_file(filepath):
    ext = filepath.rsplit(".", 1)[1].lower()
    if ext == "csv":
        return pd.read_csv(filepath)
    elif ext in ["xlsx", "xls"]:
        return pd.read_excel(filepath)
    elif ext == "json":
        return pd.read_json(filepath)
    return None


def reconcile_invoices(df1, df2):
    results = {
        "summary": {},
        "matched": [],
        "unmatched_source1": [],
        "unmatched_source2": [],
        "discrepancies": [],
    }

    df1.columns = [c.strip().lower().replace(" ", "_") for c in df1.columns]
    df2.columns = [c.strip().lower().replace(" ", "_") for c in df2.columns]

    key_candidates = ["invoice_id", "invoice_number", "invoice_no", "id", "ref", "reference"]
    key_col = None
    for k in key_candidates:
        if k in df1.columns and k in df2.columns:
            key_col = k
            break
    if key_col is None:
        common = list(set(df1.columns) & set(df2.columns))
        if common:
            key_col = common[0]

    amount_candidates = ["amount", "total", "value", "invoice_amount", "net_amount", "gross_amount"]
    amount_col = None
    for a in amount_candidates:
        if a in df1.columns and a in df2.columns:
            amount_col = a
            break

    if key_col is None:
        return {"error": "No matching key column found. Ensure both files share a common ID column (e.g., invoice_id, invoice_number)."}

    df1_ids = set(df1[key_col].astype(str))
    df2_ids = set(df2[key_col].astype(str))
    matched_ids = df1_ids & df2_ids
    only_in_1 = df1_ids - df2_ids
    only_in_2 = df2_ids - df1_ids

    for inv_id in matched_ids:
        row1 = df1[df1[key_col].astype(str) == inv_id].iloc[0]
        row2 = df2[df2[key_col].astype(str) == inv_id].iloc[0]
        match_info = {key_col: inv_id}
        has_discrepancy = False
        if amount_col:
            try:
                amt1 = float(str(row1[amount_col]).replace(",", "").replace("$", "").strip())
                amt2 = float(str(row2[amount_col]).replace(",", "").replace("$", "").strip())
                match_info["amount_source1"] = round(amt1, 2)
                match_info["amount_source2"] = round(amt2, 2)
                match_info["difference"] = round(amt2 - amt1, 2)
                if abs(amt1 - amt2) > 0.01:
                    has_discrepancy = True
            except:
                match_info["amount_source1"] = str(row1.get(amount_col, "N/A"))
                match_info["amount_source2"] = str(row2.get(amount_col, "N/A"))
        if has_discrepancy:
            results["discrepancies"].append(match_info)
        else:
            results["matched"].append(match_info)

    for inv_id in only_in_1:
        row = df1[df1[key_col].astype(str) == inv_id].iloc[0]
        info = {key_col: inv_id}
        if amount_col and amount_col in row.index:
            info["amount"] = str(row[amount_col])
        results["unmatched_source1"].append(info)

    for inv_id in only_in_2:
        row = df2[df2[key_col].astype(str) == inv_id].iloc[0]
        info = {key_col: inv_id}
        if amount_col and amount_col in row.index:
            info["amount"] = str(row[amount_col])
        results["unmatched_source2"].append(info)

    total = len(matched_ids) + len(only_in_1) + len(only_in_2)
    results["summary"] = {
        "key_column_used": key_col,
        "amount_column_used": amount_col or "Not found",
        "total_source1": len(df1),
        "total_source2": len(df2),
        "matched": len(results["matched"]),
        "discrepancies": len(results["discrepancies"]),
        "unmatched_source1": len(results["unmatched_source1"]),
        "unmatched_source2": len(results["unmatched_source2"]),
        "reconciliation_rate": round((len(results["matched"]) / max(total, 1)) * 100, 1),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    return results


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/upload", methods=["POST"])
def upload_files():
    if "file1" not in request.files or "file2" not in request.files:
        return jsonify({"error": "Both files are required."}), 400
    file1 = request.files["file1"]
    file2 = request.files["file2"]
    if file1.filename == "" or file2.filename == "":
        return jsonify({"error": "Please select both files."}), 400
    if not (allowed_file(file1.filename) and allowed_file(file2.filename)):
        return jsonify({"error": "Only CSV, Excel (.xlsx/.xls), or JSON files are allowed."}), 400

    uid = str(uuid.uuid4())[:8]
    path1 = os.path.join(app.config["UPLOAD_FOLDER"], secure_filename(f"{uid}_1_{file1.filename}"))
    path2 = os.path.join(app.config["UPLOAD_FOLDER"], secure_filename(f"{uid}_2_{file2.filename}"))
    file1.save(path1)
    file2.save(path2)

    try:
        df1 = load_file(path1)
        df2 = load_file(path2)
        if df1 is None or df2 is None:
            return jsonify({"error": "Could not read one or both files."}), 400
        results = reconcile_invoices(df1, df2)
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": f"Processing error: {str(e)}"}), 500
    finally:
        for p in [path1, path2]:
            if os.path.exists(p):
                os.remove(p)


if __name__ == "__main__":
    print("\n" + "=" * 52)
    print("   ⚡  Invoice Reconciliation Agent")
    print("   🌐  Open: http://localhost:5000")
    print("   📂  Upload fresh files anytime!")
    print("=" * 52 + "\n")
    app.run(debug=True, port=5000)
