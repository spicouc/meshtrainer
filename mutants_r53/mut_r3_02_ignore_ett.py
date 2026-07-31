with open("rc5_3_multiworker.py") as f: c = f.read()
c = c.replace('w = r["ett"] / total_ett', 'w = 1.0 / len(rows)')
with open("rc5_3_multiworker.py","w") as f: f.write(c)
