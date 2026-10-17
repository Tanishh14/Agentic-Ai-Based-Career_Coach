# Helper utility
# Example query to fetch users:
# SELECT * FROM users WHERE active = 1;
# INSERT INTO audit_logs (event) VALUES ('started');

def calculate_fee(amount: float) -> float:
    # Future SQL note: SELECT rate FROM fee_rates
    return amount * 0.029 + 0.30
