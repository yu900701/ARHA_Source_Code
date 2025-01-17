from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import json
from pathlib import Path

app = FastAPI()

# 定義 JSON 檔案的路徑
service_file_path = Path("./information/service.json")
subscription_file_path = Path("./information/subscription.json")
service_spec_file_path = Path("./information/serviceSpec.json")

def load_json(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            data = json.load(file)
            if not data:  # 檢查 JSON 檔案是否為空
                return None
            return data
    except json.JSONDecodeError:  # 如果 JSON 檔案是空的或格式錯誤
        return None

@app.get("/", response_class=HTMLResponse)
async def read_json():
    # 讀取 JSON 檔案
    service_data = load_json(service_file_path)
    subscription_data = load_json(subscription_file_path)
    service_spec_data = load_json(service_spec_file_path)
    
    # 將 JSON 資料轉換為 HTML 表格格式
    def json_to_table(json_data):
        if isinstance(json_data, list) and json_data:
            headers = json_data[0].keys()
            rows = [item.values() for item in json_data]
            table = "<table border='1'><tr>{}</tr>{}</table>".format(
                "".join(f"<th>{header}</th>" for header in headers),
                "".join(
                    "<tr>{}</tr>".format("".join(f"<td>{value}</td>" for value in row))
                    for row in rows
                )
            )
            return table
        return ""

    service_table = json_to_table(service_data) if service_data else ""
    subscription_table = json_to_table(subscription_data) if subscription_data else ""
    service_spec_table = json_to_table(service_spec_data) if service_spec_data else ""
    
    # 組裝最終的 HTML 內容
    html_content = "<html><body>"
    
    if service_table:
        html_content += f"<h1>Service Data</h1>{service_table}"
    if subscription_table:
        html_content += f"<h1>Subscription Data</h1>{subscription_table}"
    if service_spec_table:
        html_content += f"<h1>Service Spec Data</h1>{service_spec_table}"
    
    html_content += "</body></html>"
    
    return HTMLResponse(content=html_content)


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=5001)