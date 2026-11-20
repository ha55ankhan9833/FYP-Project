"""
Interactive teacher login for local setup scripts.

Run it yourself; it prompts for email and password (password hidden),
logs in through the normal /api/teacher/login endpoint on localhost,
checks the account is the expected teacher, and writes the access token
to a file. The token and password are never printed.

    python fixtures/teacher_login.py <token_file> <expected_teacher_id>
"""

import getpass
import sys

import requests

LOGIN_URL = "http://127.0.0.1:5000/api/teacher/login"


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: teacher_login.py <token_file> <expected_teacher_id>")

    token_file, expected = sys.argv[1], sys.argv[2]
    email = input("Teacher email: ").strip()
    password = getpass.getpass("Teacher password (hidden): ")

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
    teacher_id = str(data.get("teacher", {}).get("teacher_id", ""))

    if teacher_id != expected:
        sys.exit(f"Logged in as a different teacher; expected {expected}. "
                 "Token NOT saved.")

    with open(token_file, "w", encoding="utf-8") as handle:
        handle.write(data["access_token"])

    print(f"Login OK for {teacher_id}. Token saved (not displayed). "
          "It expires in 2 hours.")


if __name__ == "__main__":
    main()
