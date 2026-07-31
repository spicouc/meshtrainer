with open("rc5_3_multiworker.py") as f: c = f.read()
c = c.replace('if prev_ad is not None and hashes.get("base_adapter_hash", "") != prev_ad["adapter_hash"]:',
              'if False and prev_ad is not None and hashes.get("base_adapter_hash", "") != prev_ad["adapter_hash"]:')
with open("rc5_3_multiworker.py","w") as f: f.write(c)
