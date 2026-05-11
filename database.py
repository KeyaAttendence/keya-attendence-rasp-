import sqlite3
import psycopg2
from psycopg2.extras import RealDictCursor
import os
import datetime
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv('DATABASE_URL')
SQLITE_PATH = os.path.join(os.path.dirname(__file__), 'data', 'attendance.db')

db_pool = None
if DB_URL:
    from psycopg2 import pool
    # Connection pool to drastically reduce connection time to remote database
    db_pool = pool.ThreadedConnectionPool(1, 10, DB_URL)

def get_local_db():
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_db_connection():
    if DB_URL:
        # PostgreSQL (Supabase) using Pool
        try:
            conn = db_pool.getconn()
            return conn
        except Exception as e:
            print(f"Error getting remote connection: {e}")
            return get_local_db()
    else:
        # Local SQLite
        return get_local_db()

def release_db_connection(conn):
    if DB_URL:
        db_pool.putconn(conn)
    else:
        conn.close()

def get_cursor(conn):
    if DB_URL:
        return conn.cursor(cursor_factory=RealDictCursor)
    return conn.cursor()

def get_placeholder():
    return "%s" if DB_URL else "?"

def init_db():
    # 1. ALWAYS initialize Local SQLite
    local_conn = get_local_db()
    local_cursor = local_conn.cursor()
    
    local_cursor.execute('''
        CREATE TABLE IF NOT EXISTS employees (
            employee_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            department TEXT,
            phone TEXT,
            email TEXT,
            face_encoding BLOB NOT NULL
        )
    ''')
    local_cursor.execute('''
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id TEXT NOT NULL,
            date TEXT NOT NULL,
            login_time TEXT,
            logout_time TEXT,
            synced INTEGER DEFAULT 0,
            FOREIGN KEY (employee_id) REFERENCES employees (employee_id)
        )
    ''')
    
    # Check if synced column exists (migration)
    try:
        local_cursor.execute("ALTER TABLE attendance ADD COLUMN synced INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
        
    local_cursor.execute('CREATE INDEX IF NOT EXISTS idx_attendance_date ON attendance(date)')
    local_cursor.execute('CREATE INDEX IF NOT EXISTS idx_attendance_emp_date ON attendance(employee_id, date)')
    
    local_conn.commit()
    local_conn.close()

    # 2. Initialize Remote PostgreSQL (if configured)
    if DB_URL:
        remote_conn = None
        try:
            remote_conn = db_pool.getconn()
            remote_cursor = remote_conn.cursor()
            
            remote_cursor.execute('''
                CREATE TABLE IF NOT EXISTS employees (
                    employee_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    department TEXT,
                    phone TEXT,
                    email TEXT,
                    face_encoding BYTEA NOT NULL
                )
            ''')
            remote_cursor.execute('''
                CREATE TABLE IF NOT EXISTS attendance (
                    id SERIAL PRIMARY KEY,
                    employee_id TEXT NOT NULL,
                    date TEXT NOT NULL,
                    login_time TEXT,
                    logout_time TEXT,
                    FOREIGN KEY (employee_id) REFERENCES employees (employee_id)
                )
            ''')
            remote_cursor.execute('CREATE INDEX IF NOT EXISTS idx_attendance_date ON attendance(date)')
            remote_cursor.execute('CREATE INDEX IF NOT EXISTS idx_attendance_emp_date ON attendance(employee_id, date)')
            
            remote_conn.commit()
            print("Remote Database Initialized.")
        except Exception as e:
            print(f"Remote DB Init Error: {e}")
            if remote_conn:
                remote_conn.rollback()
        finally:
            if remote_conn:
                db_pool.putconn(remote_conn)

# Helper functions
def add_employee(employee_id, name, department, phone, email, face_encoding_bytes):
    conn = get_db_connection()
    cursor = get_cursor(conn)
    p = get_placeholder()
    try:
        blob = psycopg2.Binary(face_encoding_bytes) if DB_URL else face_encoding_bytes
        cursor.execute(f'''
            INSERT INTO employees (employee_id, name, department, phone, email, face_encoding)
            VALUES ({p}, {p}, {p}, {p}, {p}, {p})
        ''', (employee_id, name, department, phone, email, blob))
        conn.commit()
        return True
    except (sqlite3.IntegrityError, psycopg2.IntegrityError):
        return False
    except Exception as e:
        print(f"Error adding employee: {e}")
        return False
    finally:
        release_db_connection(conn)

def get_all_employees():
    conn = get_db_connection()
    cursor = get_cursor(conn)
    cursor.execute('SELECT * FROM employees')
    employees = cursor.fetchall()
    release_db_connection(conn)
    return employees

def get_all_employees_no_blob():
    conn = get_db_connection()
    cursor = get_cursor(conn)
    # Exclude face_encoding (BLOB) for faster GUI loads
    cursor.execute('SELECT employee_id, name, department, phone, email FROM employees')
    employees = cursor.fetchall()
    release_db_connection(conn)
    return employees

def update_employee(old_eid, new_eid, name, department, phone, email):
    conn = get_db_connection()
    cursor = get_cursor(conn)
    p = get_placeholder()
    try:
        # If ID changed, we must update attendance records with FK considerations
        if old_eid != new_eid:
            # 1. Get the face encoding from the old record
            cursor.execute(f"SELECT face_encoding FROM employees WHERE employee_id = {p}", (old_eid,))
            old_emp = cursor.fetchone()
            if not old_emp:
                return False
            
            face_encoding = old_emp['face_encoding'] if isinstance(old_emp, dict) else old_emp[0]
            
            # 2. Insert new employee record with new ID
            cursor.execute(f'''
                INSERT INTO employees (employee_id, name, department, phone, email, face_encoding)
                VALUES ({p}, {p}, {p}, {p}, {p}, {p})
            ''', (new_eid, name, department, phone, email, face_encoding))
            
            # 3. Update all attendance records to the new ID
            cursor.execute(f"UPDATE attendance SET employee_id = {p} WHERE employee_id = {p}", (new_eid, old_eid))
            
            # 4. Delete the old employee record
            cursor.execute(f"DELETE FROM employees WHERE employee_id = {p}", (old_eid,))
        else:
            # Standard update (ID hasn't changed)
            cursor.execute(f'''
                UPDATE employees 
                SET name = {p}, department = {p}, phone = {p}, email = {p}
                WHERE employee_id = {p}
            ''', (name, department, phone, email, old_eid))
        
        conn.commit()
        return True
    except Exception as e:
        if conn: conn.rollback()
        print(f"Error updating employee: {e}")
        return False
    finally:
        release_db_connection(conn)

def delete_employee(employee_id):
    conn = get_db_connection()
    cursor = get_cursor(conn)
    p = get_placeholder()
    # Delete child records first to respect Foreign Key constraints
    cursor.execute(f'DELETE FROM attendance WHERE employee_id = {p}', (employee_id,))
    cursor.execute(f'DELETE FROM employees WHERE employee_id = {p}', (employee_id,))
    conn.commit()
    release_db_connection(conn)

def mark_attendance(employee_id):
    # ALWAYS use local DB for marking to ensure speed
    conn = get_local_db()
    cursor = conn.cursor()
    today = datetime.date.today().strftime('%Y-%m-%d')
    now_time = datetime.datetime.now().strftime('%H:%M:%S')

    cursor.execute('''
        SELECT id, login_time, logout_time FROM attendance 
        WHERE employee_id = ? AND date = ?
    ''', (employee_id, today))
    
    record = cursor.fetchone()

    if not record:
        print(f"DEBUG: No record found for {employee_id} today ({today}). Creating new Check-In.")
        # First scan of the day -> Check-In
        cursor.execute('''
            INSERT INTO attendance (employee_id, date, login_time, logout_time, synced)
            VALUES (?, ?, ?, ?, 0)
        ''', (employee_id, today, now_time, ""))
        conn.commit()
        conn.close()
        return "IN", f"Check-In: {now_time}"
    else:
        rid, login_val, logout_val = record[0], record[1], record[2]
        print(f"DEBUG: Record found for {employee_id} on {today}. Login: {login_val}, Logout: {logout_val}. Updating Logout.")

        # Safety: If manual override is active, don't update
        if login_val in ['Absent', 'Sick Leave', 'Paid Leave', 'Company Holiday']:
            conn.close()
            return "OVERRIDE", "Manual Leave Active"

        # Every scan after the first one of the day will update the logout time.

        cursor.execute("UPDATE attendance SET logout_time = ?, synced = 0 WHERE id = ?", (now_time, rid))
        conn.commit()
        conn.close()
        
        if logout_val and logout_val != "":
            return "OUT", f"Check-Out Updated: {now_time}"
            
        return "OUT", f"Check-Out: {now_time}"

def get_attendance_logs(date=None, limit=None, offset=None):
    conn = get_db_connection()
    cursor = get_cursor(conn)
    p = get_placeholder()
    if date:
        query = f'''
            SELECT a.id, a.employee_id, e.name, e.department, a.date, a.login_time, a.logout_time 
            FROM attendance a 
            JOIN employees e ON a.employee_id = e.employee_id
            WHERE a.date = {p}
            ORDER BY a.login_time DESC
        '''
        if limit:
            query += f" LIMIT {limit}"
        if offset:
            query += f" OFFSET {offset}"
        cursor.execute(query, (date,))
    else:
        query = '''
            SELECT a.id, a.employee_id, e.name, e.department, a.date, a.login_time, a.logout_time 
            FROM attendance a 
            JOIN employees e ON a.employee_id = e.employee_id
            ORDER BY a.date DESC, a.login_time DESC
        '''
        if limit:
            query += f" LIMIT {limit}"
        if offset:
            query += f" OFFSET {offset}"
        cursor.execute(query)
    logs = cursor.fetchall()
    release_db_connection(conn)
    return logs

def get_attendance_logs_count(date=None):
    conn = get_db_connection()
    cursor = get_cursor(conn)
    p = get_placeholder()
    if date:
        cursor.execute(f"SELECT COUNT(*) FROM attendance WHERE date = {p}", (date,))
    else:
        cursor.execute("SELECT COUNT(*) FROM attendance")
    
    count_row = cursor.fetchone()
    # Handle dict or tuple
    count = count_row['count'] if isinstance(count_row, dict) and 'count' in count_row else count_row[0]
    release_db_connection(conn)
    return count

def update_attendance_time(employee_id, date, login_time, logout_time):
    # ALWAYS update local first and set synced=0 for background sync to pick it up
    conn = get_local_db()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            UPDATE attendance 
            SET login_time = ?, logout_time = ?, synced = 0 
            WHERE employee_id = ? AND date = ?
        ''', (login_time, logout_time, employee_id, date))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error updating attendance time: {e}")
        return False
    finally:
        conn.close()

def delete_attendance_record(employee_id, date):
    # 1. ALWAYS delete from Local SQLite
    local_conn = get_local_db()
    local_cursor = local_conn.cursor()
    employee_id = employee_id.strip()
    date = date.strip()
    
    try:
        print(f"DEBUG: Attempting to delete local record for {employee_id} on {date}")
        local_cursor.execute("DELETE FROM attendance WHERE employee_id = ? AND date = ?", (employee_id, date))
        rows_deleted = local_cursor.rowcount
        local_conn.commit()
        print(f"DEBUG: Local rows deleted: {rows_deleted}")
        
        # 2. If remote exists, delete from there too
        if DB_URL:
            remote_conn = None
            try:
                print(f"DEBUG: Attempting to delete remote record for {employee_id} on {date}")
                remote_conn = db_pool.getconn()
                remote_cursor = remote_conn.cursor()
                remote_cursor.execute("DELETE FROM attendance WHERE employee_id = %s AND date = %s", (employee_id, date))
                remote_rows = remote_cursor.rowcount
                remote_conn.commit()
                print(f"DEBUG: Remote rows deleted: {remote_rows}")
            except Exception as re:
                print(f"DEBUG: Remote delete error: {re}")
            finally:
                if remote_conn:
                    db_pool.putconn(remote_conn)
                    
        return True
    except Exception as e:
        print(f"Error deleting attendance record: {e}")
        return False
    finally:
        local_conn.close()

def get_unsynced_attendance():
    conn = get_local_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM attendance WHERE synced = 0')
    rows = cursor.fetchall()
    conn.close()
    return rows

def mark_as_synced(local_id):
    conn = get_local_db()
    cursor = conn.cursor()
    cursor.execute('UPDATE attendance SET synced = 1 WHERE id = ?', (local_id,))
    conn.commit()
    conn.close()

def sync_attendance_to_supabase():
    """
    Background task to sync local attendance records to Supabase.
    """
    if not DB_URL:
        return
    
    unsynced = get_unsynced_attendance()
    if not unsynced:
        return

    print(f"Syncing {len(unsynced)} records to Supabase...")
    
    remote_conn = None
    try:
        remote_conn = db_pool.getconn()
        remote_cursor = remote_conn.cursor()
        
        for row in unsynced:
            # row format: (id, employee_id, date, login_time, logout_time, synced)
            lid, eid, dt, login, logout, _ = row
            
            # Check if record already exists on remote
            remote_cursor.execute("SELECT id FROM attendance WHERE employee_id = %s AND date = %s", (eid, dt))
            remote_record = remote_cursor.fetchone()
            
            if remote_record:
                # Update
                remote_cursor.execute(
                    "UPDATE attendance SET login_time = %s, logout_time = %s WHERE employee_id = %s AND date = %s",
                    (login, logout, eid, dt)
                )
            else:
                # Insert
                remote_cursor.execute(
                    "INSERT INTO attendance (employee_id, date, login_time, logout_time) VALUES (%s, %s, %s, %s)",
                    (eid, dt, login, logout)
                )
            
            remote_conn.commit()
            mark_as_synced(lid)
            
        print("Sync complete.")
    except Exception as e:
        print(f"Sync error: {e}")
        if remote_conn:
            remote_conn.rollback()
    finally:
        if remote_conn:
            db_pool.putconn(remote_conn)
