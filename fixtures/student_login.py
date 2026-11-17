"""
Interactive STUDENT login for the end-to-end test.

Run it yourself, only for a student account you are authorized to use.
It prompts for email and password (password hidden), logs in through the
normal /api/student/login endpoint on localhost, checks the account is the
expected student, and writes the access token to a file. The token and
password are never printed.

    python fixtures/student_login.py <token_file> <expected_student_id>
"""

import getpass
import sys

import requests

LOGIN_URL = "http://127.0.0.1:5000/api/student/login"


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: student_login.py <token_file> <expected_student_id>")

    token_file, expected = sys.argv[1], sys.argv[2]
    email = input("Student email: ").strip()
    password = getpass.getpass("Student password (hidden): ")

    try:
        response = requests.post(LOGIN_URL,
                                 json={"email": email, "password": password},
                                 timeout=30)
    except requests.RequestException as error:
        sys.exit(f"Login request failed: {type(error).__name__}")
    finally:
        password = None

    if response.status_code != 200:
        sys.exit(f"Login failed (HTTP {response.status_code})")

    data = response.json()
    student_id = str(data.get("student", {}).get("student_id", ""))

    if student_id != expected:
        sys.exit(f"Logged in as a different student; expected {expected}. "
                 "Token NOT saved.")

    with open(token_file, "w", encoding="utf-8") as handle:
        handle.write(data["access_token"])

    print(f"Login OK for {student_id}. Token saved (not displayed). "
          "It expires in 2 hours.")


if __name__ == "__main__":
    main()
