import csv


def column_sum(path, column):
    total = 0.0
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            total += float(row[column])
    return total
