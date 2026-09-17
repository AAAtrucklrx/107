import json
full = json.loads(open(r"f:\小蜗\scripts\data\jw_api_full.json", encoding="utf-8").read())
print("教学周接口响应:", full["/home/get-current-teach-week"]["structure"])
exam = json.loads(open(r"f:\小蜗\scripts\data\jw_exam_capture.json", encoding="utf-8").read())
for url, info in exam.items():
    if "datum" in url:
        print("\ndatum 响应结构头:", info["structure"][:340])
