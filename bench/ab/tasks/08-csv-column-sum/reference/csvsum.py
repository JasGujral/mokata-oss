import csv


def column_sum(path, column):
    total = 0.0
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        if column not in (reader.fieldnames or []):
            raise KeyError(column)
        for row in reader:
            try:
                total += float(row[column])
            except (TypeError, ValueError):
                continue
    return total
