import sys; sys.path.insert(0,".")
with open("rc5_2_split_server.py") as f: c = f.read()
c = c.replace("result["cut_gradient"]", "result["cut_gradient"][0,0,0] = result["cut_gradient"][0,0,0] + 0.1; result["cut_gradient"]")
with open("rc5_2_split_server.py","w") as f: f.write(c)
