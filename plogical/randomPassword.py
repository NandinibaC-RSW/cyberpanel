import string
import secrets

def generate_pass(length=14):
    chars = string.ascii_uppercase + string.ascii_lowercase + string.digits
    return ''.join(secrets.choice(chars) for _ in range(length))