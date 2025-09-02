
def csv_to_list(csv_str: str):
    if not csv_str:
        return []
    return [s.strip() for s in csv_str.split(",") if s.strip()]

def list_to_csv(lst):
    return ",".join(lst or [])
