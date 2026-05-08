#!/usr/bin/env python3
"""Test database connection"""

try:
    import psycopg2
    from dotenv import load_dotenv
    import os
    
    load_dotenv()
    
    print("Testing PostgreSQL connection...")
    
    # Get connection parameters from environment or use defaults
    conn_params = {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': os.getenv('DB_PORT', '5432'),
        'database': os.getenv('DB_NAME', 'attendance_db'),
        'user': os.getenv('DB_USER', 'postgres'),
        'password': os.getenv('DB_PASSWORD', '')
    }
    
    print(f"Connecting to: {conn_params['host']}:{conn_params['port']}/{conn_params['database']}")
    
    # Try to connect
    conn = psycopg2.connect(**conn_params)
    conn.close()
    
    print("✅ Database connection successful!")
    
except psycopg2.OperationalError as e:
    print(f"⚠️  Database not ready: {e}")
    print("Please ensure PostgreSQL is installed and running, and database is created.")
except Exception as e:
    print(f"❌ Error: {e}")

print("\nTo run your main application:")
print("  python app.py")
print("OR")
print("  gunicorn app:app")
