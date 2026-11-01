
import sqlite3

connection = sqlite3.connect("users.db")

tables = connection.execute("""
    SELECT name FROM sqlite_master
    WHERE type = 'table'
""").fetchall()

print("Tables:", tables)

for table in tables:
    table_name = table[0]
    if table_name == "users":
        columns = connection.execute(
            "PRAGMA table_info(users)"
        ).fetchall()
        print("Users table columns:")
        for column in columns:
            print(column)

connection.close()
