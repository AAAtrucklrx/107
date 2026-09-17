import json, re
s = open(r"f:\小蜗\scripts\data\tree_2763_search.json", encoding="utf-8").read()
locs = [m.start() for m in re.finditer("英语通修", s)]
print("出现次数:", len(locs))
for pos in locs[:5]:
    print("...", s[max(0,pos-150):pos+80].replace("\n", " "), "\n")
