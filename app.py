from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for, session
from functools import wraps
import face_recognition
import cv2
import numpy as np
import os
import pandas as pd
from datetime import datetime
import base64
import threading
import time

from database import init_db, add_employee, update_employee, get_all_employees, get_all_employees_no_blob, delete_employee, mark_attendance, get_attendance_logs, get_attendance_logs_count, update_attendance_time, get_db_connection, get_cursor, get_placeholder, sync_attendance_to_supabase
from face_utils import encode_face_from_image, serialize_encoding, deserialize_encoding, match_face

app = Flask(__name__)
app.config['SECRET_KEY'] = 'fas-secret-2026'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB limit

# ─── Database Init ────────────────────────────────────────────────────────────
init_db()

# ─── In-memory face cache ─────────────────────────────────────────────────────
known_encodings = []
known_ids = []
id_to_name = {}

def reload_faces():
    global known_encodings, known_ids, id_to_name
    known_encodings, known_ids = [], []
    id_to_name = {}
    for emp in get_all_employees():
        eid = emp['employee_id']
        name = emp['name']
        encoding = deserialize_encoding(emp['face_encoding'])
        if encoding is not None:
            known_ids.append(eid)
            known_encodings.append(encoding)
            id_to_name[eid] = name
        else:
            print(f"Skipping employee {eid} ({name}) due to invalid face encoding.")

reload_faces()

# ─── Helpers ──────────────────────────────────────────────────────────────────
def b64_to_bytes(b64str):
    if ',' in b64str:
        b64str = b64str.split(',', 1)[1]
    return base64.b64decode(b64str)

# ─── Auth ─────────────────────────────────────────────────────────────────────
ADMIN_EMAIL = os.getenv('ADMIN_EMAIL', 'admin@keyafusion.com')
ADMIN_PASS = os.getenv('ADMIN_PASS', 'admin123')

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        data = request.json or {}
        email = data.get('email')
        password = data.get('password')
        if email == ADMIN_EMAIL and password == ADMIN_PASS:
            session['logged_in'] = True
            return jsonify({"success": True, "message": "Welcome back!"})
        return jsonify({"success": False, "message": "Invalid credentials"}), 401
    
    if session.get('logged_in'):
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

@app.route('/send_otp', methods=['POST'])
def send_otp():
    return jsonify({"success": False, "message": "Admin registration is disabled. Please sign in."}), 403

@app.route('/register', methods=['POST'])
def register():
    return jsonify({"success": False, "message": "Admin registration is disabled."}), 403

@app.route('/')
@login_required
def index():
    # If this service is meant only for users, redirect home to the user panel
    if os.getenv('APP_MODE') == 'USER':
        return redirect(url_for('user_panel'))
        
    employees = get_all_employees_no_blob()
    today      = datetime.now().strftime('%Y-%m-%d')
    today_logs = get_attendance_logs(date=today)
    
    present_eids = {l['employee_id'] for l in today_logs if l['login_time'] != 'Absent'}
    present_employees = [e for e in employees if e['employee_id'] in present_eids]
    absent_employees = [e for e in employees if e['employee_id'] not in present_eids]
    
    recent_logs = get_attendance_logs(limit=8)
    
    return render_template('index.html', 
                           total_employees=len(employees),
                           present_today=len(present_employees),
                           absent_today=len(absent_employees),
                           present_list=present_employees,
                           absent_list=absent_employees,
                           recent_logs=recent_logs)

@app.route('/employees')
@login_required
def view_employees():
    return render_template('employees.html', employees=get_all_employees_no_blob())

@app.route('/add_employee')
@login_required
def add_employee_route():
    return render_template('add_employee.html')

@app.route('/attendance')
@login_required
def attendance():
    return render_template('attendance.html')

@app.route('/history')
@login_required
def history():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    offset = (page - 1) * per_page
    
    logs = get_attendance_logs(limit=per_page, offset=offset)
    total_logs = get_attendance_logs_count()
    
    import math
    total_pages = math.ceil(total_logs / per_page) if total_logs > 0 else 1
    
    return render_template('history.html', logs=logs, page=page, total_pages=total_pages, total_logs=total_logs)

@app.route('/user')
def user_panel():
    return render_template('user_attendance.html')

@app.route('/api/last_activity')
def last_activity():
    conn = get_db_connection()
    cursor = get_cursor(conn)
    
    try:
        # Get the single most recent activity (order by date then latest time)
        # Note: We check both login_time and logout_time to find the absolute latest entry
        cursor.execute(f"""
            SELECT e.name, a.login_time, a.logout_time, a.date
            FROM attendance a 
            JOIN employees e ON a.employee_id = e.employee_id 
            WHERE a.login_time != 'Absent' AND a.login_time != ''
            ORDER BY a.date DESC, a.login_time DESC, a.logout_time DESC LIMIT 1
        """)
        row = cursor.fetchone()
        
        if row:
            # Determine if the latest action was a logout or login
            status = "Logged In"
            time = row['login_time']
            if row['logout_time'] and row['logout_time'] != '---' and row['logout_time'] != '':
                status = "Logged Out"
                time = row['logout_time']
                
            return jsonify({
                "success": True,
                "name": row['name'],
                "status": status,
                "time": time
            })
    except Exception as e:
        print(f"API Error: {e}")
    finally:
        release_db_connection(conn)
        
    return jsonify({"success": False})

@app.route('/manifest.json')
def serve_manifest():
    return send_file('static/manifest.json')

@app.route('/sw.js')
def serve_sw():
    return send_file('static/sw.js')

@app.route('/my-attendance')
@app.route('/my_attendance')
@app.route('/my-attendence')
@app.route('/my_attendence')
def my_attendance():
    """ 
    Public route for employees to view their own attendance records.
    Authentication is handled on the frontend via localStorage ID or a search form.
    """
    return render_template('my_attendance.html')

# ─── API: Add employee with live face samples ──────────────────────────────────
@app.route('/api/add_employee', methods=['POST'])
def api_add_employee():
    try:
        data = request.get_json(force=True)
    except Exception as e:
        print(f"Error parsing JSON: {e}")
        return jsonify(success=False, message=f'Invalid request: {e}')

    eid        = (data.get('employee_id') or '').strip()
    name       = (data.get('name') or '').strip()
    department = data.get('department', 'IT')
    phone      = data.get('phone', '')
    email      = data.get('email', '')
    frames_b64 = data.get('frames', [])   # list of base64 strings

    if not eid or not name:
        return jsonify(success=False, message='Employee ID and Name are required.')
    if not frames_b64:
        return jsonify(success=False, message='No face frames received.')

    # Extract encodings from each captured frame
    good_encodings = []
    for b64 in frames_b64:
        enc = encode_face_from_image(b64_to_bytes(b64))
        if enc is not None:
            good_encodings.append(enc)

    if len(good_encodings) < 3:
        return jsonify(success=False,
                       message=f'Only {len(good_encodings)} valid face frames detected out of {len(frames_b64)}. '
                               'Ensure good lighting and face is fully visible.')

    # Average the encodings → robust single representation
    avg_encoding = np.mean(good_encodings, axis=0)
    blob = serialize_encoding(avg_encoding)

    ok = add_employee(eid, name, department, phone, email, blob)
    if not ok:
        return jsonify(success=False, message='Employee ID already exists. Use a different ID.')

    reload_faces()
    return jsonify(success=True,
                   message=f'✅ {name} registered with {len(good_encodings)} face samples!')

# ─── API: Delete employee ──────────────────────────────────────────────────────
@app.route('/api/delete_employee/<eid>', methods=['POST'])
@login_required
def api_delete_employee(eid):
    delete_employee(eid)
    reload_faces()
    return jsonify(success=True)

@app.route('/api/update_employee', methods=['POST'])
@login_required
def api_update_employee():
    data = request.get_json(force=True)
    old_eid = data.get('old_employee_id')
    new_eid = data.get('employee_id')
    name = data.get('name')
    dept = data.get('department')
    phone = data.get('phone')
    email = data.get('email')
    
    if not old_eid or not new_eid or not name:
        return jsonify(success=False, message="Employee ID and Name are required.")
    
    ok = update_employee(old_eid, new_eid, name, dept, phone, email)
    if ok:
        reload_faces()
        return jsonify(success=True, message="Employee updated successfully.")
    return jsonify(success=False, message="Failed to update employee.")


# ─── API: Recognise a single frame ────────────────────────────────────────────
@app.route('/api/recognise', methods=['POST'])
def api_recognise():
    data = request.get_json(force=True)
    img_b64 = data.get('frame', '')
    if not img_b64:
        return jsonify(success=False, faces=[])

    img_bytes = b64_to_bytes(img_b64)
    nparr = np.frombuffer(img_bytes, np.uint8)
    bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if bgr is None:
        return jsonify(success=False, faces=[])

    # ───── Speed & Accuracy Optimization (Hybrid Mode) ─────
    # 1. Faster Detection: 50% scale (320x240 is ideal for HOG)
    small = cv2.resize(bgr, (0, 0), fx=0.5, fy=0.5)
    small_rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
    
    # 2. Extract locations from thumbnail
    # Upsample once is slow on CPU, set to 0 as users are typically close to the camera.
    locs = face_recognition.face_locations(small_rgb, number_of_times_to_upsample=0, model='hog')
    
    if not locs:
        return jsonify(success=True, faces=[])

    # 3. Precise Encoding: Full Scale
    # We use full resolution image for encoding to maintain accuracy
    full_rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    # Upscale locations to match original image (2x because of 0.5 scale)
    full_locs = [(t*2, r*2, b*2, l*2) for (t, r, b, l) in locs]
    # num_jitters=0 for sub-100ms processing
    encs = face_recognition.face_encodings(full_rgb, full_locs, num_jitters=0)

    results = []
    for (t, r, b, l), enc in zip(full_locs, encs):
        # We need to find the best match and the distance
        if not known_encodings:
            mid, dist = None, 1.0
        else:
            distances = face_recognition.face_distance(known_encodings, enc)
            best_idx = np.argmin(distances)
            dist = float(distances[best_idx])
            # Use strict tolerance (0.50) for high accuracy, but allow dynamic adjustment
            mid = known_ids[best_idx] if dist < 0.55 else None

        name = id_to_name.get(mid, 'Unknown') if mid else 'Unknown'
        results.append({
            'id':  mid if mid else 'Unknown',
            'name': name,
            'dist': round(dist, 4), # Helpful for frontend fine-tuning
            'box': {'top': t, 'right': r, 'bottom': b, 'left': l}
        })

    return jsonify(success=True, faces=results)


# ─── API: Mark attendance ─────────────────────────────────────────────────────
@app.route('/api/mark', methods=['POST'])
def api_mark():
    data = request.get_json(force=True)
    eid  = (data.get('employee_id') or '').strip()
    if not eid:
        return jsonify(success=False, message='No employee_id')
    
    # mark_attendance now returns (type, message)
    res = mark_attendance(eid)
    if isinstance(res, tuple):
        status_type, msg = res
    else:
        status_type, msg = "ERROR", res
        
    return jsonify(success=True, type=status_type, message=msg)

@app.route('/api/get_my_attendance', methods=['POST'])
def api_get_my_attendance():
    """ Public API for personal employee dashboard """
    data = request.get_json(force=True)
    eid = (data.get('employee_id') or '').strip()
    month = data.get('month', datetime.now().month)
    year = data.get('year', datetime.now().year)

    if not eid:
        return jsonify(success=False, message="Employee ID is required.")

    # Get employee name
    import database
    employees = database.get_all_employees_no_blob()
    emp = next((e for e in employees if e['employee_id'] == eid), None)
    name = emp['name'] if emp else "Unknown"

    # Get all logs and filter by employee and month
    all_logs = get_attendance_logs()
    filtered_logs = []
    for log in all_logs:
        try:
            log_date = datetime.strptime(log['date'], '%Y-%m-%d')
            if log['employee_id'] == eid and log_date.month == month and log_date.year == year:
                filtered_logs.append({
                    'date': log['date'],
                    'login': log['login_time'],
                    'logout': log['logout_time'],
                })
        except: continue
    
    return jsonify(success=True, name=name, logs=filtered_logs)

@app.route('/api/update_attendance_time', methods=['POST'])
@login_required
def api_update_attendance_time():
    data = request.get_json(force=True)
    eid = data.get('employee_id')
    date = data.get('date')
    login_t = data.get('login_time')
    logout_t = data.get('logout_time')
    
    if not eid or not date:
        return jsonify(success=False, message="Employee ID and Date are required.")
    
    ok = update_attendance_time(eid, date, login_t, logout_t)
    if ok:
        return jsonify(success=True, message="Attendance record updated successfully.")
    return jsonify(success=False, message="Failed to update record in database.")

# ─── API: Delete attendance record ──────────────────────────────────────────
@app.route('/api/delete_attendance_record', methods=['POST'])
@login_required
def api_delete_attendance_record():
    data = request.get_json(force=True)
    eid = data.get('employee_id')
    date = data.get('date')
    
    if not eid or not date:
        return jsonify(success=False, message="Employee ID and Date are required.")
        
    success, local_rows, remote_rows = delete_attendance_record(eid, date)
    if success:
        return jsonify({
            "success": True, 
            "local_deleted": local_rows, 
            "remote_deleted": remote_rows,
            "message": f"Deleted {local_rows} local and {remote_rows} remote records."
        })
    return jsonify(success=False, message="Failed to execute delete operation.")

# ─── API: Manual attendance override ──────────────────────────────────────────
@app.route('/api/manual_attendance', methods=['POST'])
def api_manual_attendance():
    data = request.get_json(force=True)
    eid = data.get('employee_id')
    status = data.get('status')
    date_val = data.get('date', datetime.now().strftime('%Y-%m-%d'))
    in_time_val = data.get('in_time')
    out_time_val = data.get('out_time')
    
    if not eid or not status:
        return jsonify(success=False, message='Missing parameters')
        
    from database import get_local_db
    conn = get_local_db()
    cursor = conn.cursor()
    
    # Check if record exists
    cursor.execute("SELECT id FROM attendance WHERE employee_id = ? AND date = ?", (eid, date_val))
    record = cursor.fetchone()
    
    if status == 'Present':
        in_time = (in_time_val + ":00") if in_time_val else datetime.now().strftime('%H:%M:%S')
        out_time = (out_time_val + ":00") if out_time_val else ''
    elif status == 'Absent':
        in_time = 'Absent'
        out_time = 'Absent'
    else:
        # Leave statuses (Sick Leave, Paid Leave, Company Holiday)
        in_time = status
        out_time = status

    rec_id = record['id'] if record else None
    # SQLite row object behaves like dict but accessing by 'id' might need dict-like access
    if record and type(record) is not dict:
        rec_id = record[0] if isinstance(record, tuple) else record['id']

    if record:
        cursor.execute("UPDATE attendance SET login_time = ?, logout_time = ?, synced = 0 WHERE id = ?", (in_time, out_time, rec_id))
    else:
        cursor.execute("INSERT INTO attendance (employee_id, date, login_time, logout_time, synced) VALUES (?, ?, ?, ?, 0)", (eid, date_val, in_time, out_time))
        
    conn.commit()
    conn.close()
    return jsonify(success=True)

@app.route('/api/mark_holiday_all', methods=['POST'])
@login_required
def api_mark_holiday_all():
    data = request.get_json(force=True)
    date_val = data.get('date')
    if not date_val:
        return jsonify(success=False, message='Date is required')
        
    from database import get_local_db
    conn = get_local_db()
    cursor = conn.cursor()
    
    employees = get_all_employees_no_blob()
    for emp in employees:
        eid = emp['employee_id']
        cursor.execute("SELECT id FROM attendance WHERE employee_id = ? AND date = ?", (eid, date_val))
        record = cursor.fetchone()
        
        in_time, out_time = 'Company Holiday', 'Company Holiday'
        
        rec_id = record[0] if record else None
                
        if record:
            cursor.execute("UPDATE attendance SET login_time = ?, logout_time = ?, synced = 0 WHERE id = ?", (in_time, out_time, rec_id))
        else:
            cursor.execute("INSERT INTO attendance (employee_id, date, login_time, logout_time, synced) VALUES (?, ?, ?, ?, 0)", (eid, date_val, in_time, out_time))
            
    conn.commit()
    conn.close()
    return jsonify(success=True)


# ─── Export Excel ─────────────────────────────────────────────────────────────
from openpyxl.styles import PatternFill, Font, Alignment
from openpyxl.utils import get_column_letter

@app.route('/export_excel')
@login_required
def export_excel():
    month = request.args.get('month', type=int, default=datetime.now().month)
    year  = request.args.get('year',  type=int, default=datetime.now().year)
    
    logs = get_attendance_logs()
    employees = get_all_employees_no_blob()
    
    # Setup Date Range for Selected Month
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    
    # If it's the current month, only go up to today
    today = datetime.now()
    if year == today.year and month == today.month:
        end_date = today
    else:
        end_date = datetime(year, month, last_day)
        
    start_date = datetime(year, month, 1)
    date_range = pd.date_range(start=start_date, end=end_date)
    
    # 1. Prepare Base Employee DataFrame
    emp_list = [{'ID': e['employee_id'], 'Name': e['name'], 'Department': e['department']} for e in employees]
    df_emp = pd.DataFrame(emp_list)
    
    if df_emp.empty:
      # Fallback if no employees
      df_emp = pd.DataFrame(columns=['ID', 'Name', 'Department'])
    
    # 2. Process Logs into a Dictionary
    # Key: (employee_id, date_str), Value: Status string
    attendance_dict = {}
    emp_total_ot = {} # employee_id -> total seconds
    
    threshold_t = datetime.strptime("17:45:00", "%H:%M:%S")

    for l in logs:
        try:
           log_date = datetime.strptime(l['date'], '%Y-%m-%d')
           if log_date.month == month and log_date.year == year:
               eid = l['employee_id']
               if l['login_time'] == 'Absent':
                   status = 'Absent'
               elif l['login_time'] in ['Sick Leave', 'Paid Leave', 'Company Holiday']:
                   status = l['login_time']
               else:
                   in_t = l.get('login_time', '-')
                   out_t = l.get('logout_time', '-') or '-'
                   status = f"IN: {in_t}\nOUT: {out_t}"
                   
                   # OT Calculation
                   if out_t and out_t != '-' and in_t and in_t != '-':
                       try:
                           actual_out = datetime.strptime(out_t, "%H:%M:%S")
                           actual_in = datetime.strptime(in_t, "%H:%M:%S")
                           ot_secs = 0
                           
                           is_sunday_log = log_date.weekday() == 6
                           
                           if is_sunday_log:
                               # Full time worked on Sunday is OT
                               if actual_out > actual_in:
                                   diff = actual_out - actual_in
                                   ot_secs = diff.seconds
                           else:
                               if actual_out > threshold_t:
                                   diff = actual_out - threshold_t
                                   ot_secs = diff.seconds
                                   
                           if ot_secs > 0:
                               emp_total_ot[eid] = emp_total_ot.get(eid, 0) + ot_secs
                               
                               # Format single day OT
                               h, r = divmod(ot_secs, 3600)
                               m, _ = divmod(r, 60)
                               status += f"\nOT: {h:02}:{m:02}"
                       except: pass

               attendance_dict[(eid, log_date.strftime('%Y-%m-%d'))] = status
        except Exception:
           pass
           
    # 3. Build Date Columns
    for single_date in date_range:
        date_str = single_date.strftime('%Y-%m-%d')
        col_name = single_date.strftime('%d-%b')
        is_sunday = single_date.weekday() == 6
        
        status_list = []
        for idx, row in df_emp.iterrows():
            eid = row['ID']
            if is_sunday:
                status = attendance_dict.get((eid, date_str))
                if status:
                    status_list.append(status)
                else:
                    status_list.append('Holiday')
            else:
                status = attendance_dict.get((eid, date_str), 'Absent')
                status_list.append(status)
                
        df_emp[col_name] = status_list

    # 3.1 Build Total OT, Present, Absent Columns
    total_present_list = []
    total_absent_list = []
    total_ot_list = []
    
    date_cols = df_emp.columns[3:] # Everything after ID, Name, Department are date columns
    
    for idx, row in df_emp.iterrows():
        eid = row['ID']
        
        present_count = sum(1 for c in date_cols if str(row[c]).startswith('IN:'))
        absent_count = sum(1 for c in date_cols if str(row[c]) == 'Absent')
        
        total_present_list.append(present_count)
        total_absent_list.append(absent_count)
        
        secs = emp_total_ot.get(eid, 0)
        if secs > 0:
            h, r = divmod(secs, 3600)
            m, _ = divmod(r, 60)
            total_ot_list.append(f"{h}h {m}m")
        else:
            total_ot_list.append("-")
            
    df_emp['Total Present'] = total_present_list
    df_emp['Total Absent'] = total_absent_list
    df_emp['Total Overtime'] = total_ot_list

    # 4. Save via OpenPyXL and Apply Colors
    path = os.path.join(os.path.dirname(__file__), 'data', 'Monthly_Attendance.xlsx')
    
    writer = pd.ExcelWriter(path, engine='openpyxl')
    df_emp.to_excel(writer, sheet_name='Attendance', index=False)
    
    workbook = writer.book
    worksheet = writer.sheets['Attendance']
    
    # Define Fills
    present_fill = PatternFill(start_color="D1FAE5", end_color="D1FAE5", fill_type="solid")
    absent_fill = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")
    holiday_fill = PatternFill(start_color="EEF2FF", end_color="EEF2FF", fill_type="solid")
    sick_fill = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid") # light yellow/orange
    paid_fill = PatternFill(start_color="F3E8FF", end_color="F3E8FF", fill_type="solid") # light purple
    header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    
    header_font = Font(color="FFFFFF", bold=True)
    
    # Format Headers
    for col_num, col_name in enumerate(df_emp.columns, 1):
        cell = worksheet.cell(row=1, column=col_num)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center', vertical='center')
        
        col_let = get_column_letter(col_num)
        if col_num <= 3:
           worksheet.column_dimensions[col_let].width = 20
        else:
           worksheet.column_dimensions[col_let].width = 18 # More room for IN/OUT
    
    # Format Cells based on text
    for r_idx in range(2, len(df_emp) + 2):
        worksheet.row_dimensions[r_idx].height = 42 # Much taller for better spacing
        for c_idx in range(4, len(df_emp.columns) + 1):
            cell = worksheet.cell(row=r_idx, column=c_idx)
            val = str(cell.value) if cell.value else ""
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            
            if "IN:" in val:
                cell.fill = present_fill
                cell.font = Font(color="065F46", bold=True, size=10) # Slightly larger font
            elif val == 'Absent':
                cell.fill = absent_fill
                cell.font = Font(color="991B1B", size=9)
            elif val == 'Holiday' or val == 'Company Holiday':
                cell.fill = holiday_fill
                cell.font = Font(color="3730A3", size=9)
            elif val == 'Sick Leave':
                cell.fill = sick_fill
                cell.font = Font(color="92400E", bold=True, size=9)
            elif val == 'Paid Leave':
                cell.fill = paid_fill
                cell.font = Font(color="6B21A8", bold=True, size=9)
                
    writer.close()
    
    return send_file(path, as_attachment=True, download_name=f'Attendance_Report_{today.strftime("%b_%Y")}.xlsx')

@app.route('/api/delete_attendance_record/<int:record_id>', methods=['POST'])
@login_required
def api_delete_attendance_record_old(record_id):
    # Deprecated: use /api/delete_attendance_record instead
    return jsonify(success=False, message="Use new API with employee_id and date")

def background_sync_task():
    """
    Thread that runs in background to sync local attendance to cloud.
    """
    print("Background Sync Thread Started.")
    while True:
        try:
            sync_attendance_to_supabase()
        except Exception as e:
            print(f"Background Sync Error: {e}")
        time.sleep(60) # Sync every 60 seconds

if __name__ == '__main__':
    # Kill any existing process on port 5005 (only in the main process, not the reloader)
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        import subprocess
        subprocess.run("fuser -k 5005/tcp >/dev/null 2>&1 || true", shell=True)
        
        # Start background sync thread only once
        sync_thread = threading.Thread(target=background_sync_task, daemon=True)
        sync_thread.start()
    
    app.run(host='0.0.0.0', port=5005, debug=True, threaded=True)
