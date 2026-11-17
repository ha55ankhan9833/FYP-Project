
import pyodbc

# The application database. Only the development E2E launcher
# (dev_tools/run_e2e_backend.py) and E2E tools call select_database() to
# switch this process to the isolated test copy; nothing reads it from the
# environment, so a normal start always uses FYP_Database.
DEFAULT_DATABASE = "FYP_Database"
E2E_TEST_DATABASE = "FYP_E2E_Test"
ALLOWED_DATABASES = (DEFAULT_DATABASE, E2E_TEST_DATABASE)

_ACTIVE = {"name": DEFAULT_DATABASE}


def select_database(name):
    if name not in ALLOWED_DATABASES:
        raise RuntimeError(
            f"Database must be one of {', '.join(ALLOWED_DATABASES)}")
    _ACTIVE["name"] = name
    return name


def database_name():
    return _ACTIVE["name"]


def connection_string(database=None):
    database = database or database_name()
    if database not in ALLOWED_DATABASES + ("master",):
        raise RuntimeError("Database not allowed")
    return ("DRIVER={ODBC Driver 18 for SQL Server};"
            "SERVER=DESKTOP-P87BBST;"
            f"DATABASE={database};"
            "Trusted_Connection=yes;"
            "Encrypt=yes;"
            "TrustServerCertificate=yes;")


def get_connection():
    connection = pyodbc.connect(connection_string())
    return connection


if __name__ == "__main__":
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT DB_NAME(), @@SERVERNAME")
        result = cursor.fetchone()

        print("Database Connected Successfully!")
        print("Database:", result[0])
        print("Server:", result[1])

        conn.close()

    except pyodbc.Error as error:
        print("Database Connection Failed!")
        print(error)
