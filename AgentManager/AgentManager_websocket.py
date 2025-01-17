import asyncio
import logging
import os
import threading
import time
from fastapi import FastAPI, Form, Request
import requests
import uvicorn
import paramiko
import json
import sys
import websockets

Agent_Host = sys.argv[1]
Agent_Host_ACCOUNT = sys.argv[2]
Agent_Host_PASSWORD = sys.argv[3]
port = 8888
websocket_port = 50051

Service = ["Object", "Gesture"]

incluster = True

if incluster:
    ControllerIP = "controller-service"
    ControllerPort = 80
else:
    ControllerIP = "10.52.52.126"
    ControllerPort = 30004

log_dir = "logs"
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

logging.basicConfig(filename=os.path.join(log_dir, "AgentManager.log"),
                    format='%(asctime)s %(levelname)s: %(message)s',
                    level=logging.INFO)

app = FastAPI()

@app.middleware("http")
async def log_requests(request: Request, call_next):
    log_data = {
        "client_host": request.client.host,
        "client_port": request.client.port,
        "method": request.method,
        "url": str(request.url),
    }

    logging.info(f"HTTP Request: {log_data}")

    response = await call_next(request)
    return response

@app.post("/subscribe")
async def agent(request: Request):
    client_host = request.client.host
    client_port = request.client.port

    #call a function to create a agent on agent host
    ip, port, websocket_port = create_agent()

    #store the bind information of agent and client in AR_Agent.json
    store_information(client_host, ip, port, websocket_port)

    print(ip)
    print(port)
    print(websocket_port)

    #return the ip, port of the agent that just created
    return {"IP": ip, "Port": port, "WebsocketPort": websocket_port}

@app.post("/agentfail")
async def agentfail(request: Request):
    #todo
    #find the corresponding agent
    failed_agent, failed_agentport, failed_agentwebsocketport = find_pair_information(request.client.host)
    if failed_agent == None or failed_agentport == None or failed_agentwebsocketport == None:
        logging.error(f"Agent not found for client: {request.client.host}")
        return {"status": "500", "message": "agent not found"}


    #get a new agent information, need to tell controller who's the successor
    new_agent_port, new_agent_websocketport = generate_agent_information()
    #call controller to get agent information
    body = {
        "old_ip": failed_agent,
        "old_port": failed_agentport,
        "new_ip": Agent_Host,
        "new_port": new_agent_port
    }
    response = requests.post(f'http://{ControllerIP}:{ControllerPort}/agentfail', json.dumps(body))
    if response.status_code != 200:
        logging.error("Failed to get result from controller")
        return{"status": "500", "message": "fail getting result from controller"}
    response = response.json()
    logging.info(f"got old information from controller: {response}")

    try:
        obj_ip = "0"
        obj_port = 0
        obj_freq = 0
        ges_ip = "0"
        ges_port = 0
        ges_freq = 0

        for service in response:
            servicetype = service['ServiceType']
            if servicetype == 'object':
                obj_ip = service['IP']
                obj_port = service['Port']
                obj_freq = service['Frequency']
            elif servicetype == 'gesture':
                ges_ip = service['IP']
                ges_port = service['Port']
                ges_freq = service['Frequency']
        # obj_ip = response[0].get("IP")
        # obj_port = response[0].get("Port")
        # obj_freq = response[0].get("Frequency")
        # ges_ip = response[1].get("IP")
        # ges_port = response[1].get("Port")
        # ges_freq = response[1].get("Frequency")
    except Exception:
        logging.error(f"Error parsing response: {response}")
        print(response)

    #call a function to create a agent on agent host
    ip, port, websocket_port = create_agent(obj_IP=obj_ip, obj_Port=obj_port, obj_Freq=obj_freq, ges_IP=ges_ip, ges_Port=ges_port, ges_Freq=ges_freq, newport=new_agent_port, newwebsocketport=new_agent_websocketport)

    #store the bind information of the new agent and client
    store_information(request.client.host, ip, port, websocket_port)

    return {"status": "200", "message": "OK"}

@app.get("/newagent")
async def newagent(request: Request):
    #find the pair relationship of client and agent
    ip , port, websocketport = find_pair_information(request.client.host)
    if ip == None or port == None or websocketport == None:
        ip = ""
        port = 0
        websocketport = 0

    logging.info(f"New agent info: IP={ip}, Port={port}, WebsocketPort={websocketport}")
    #return the corresponding agent ip and port
    body = {
        "IP": ip,
        "Port": port,
        "WebsocketPort": websocketport
    }
    return {"IP": ip, "Port": port, "WebsocketPort": websocketport}

def run_server():
    #the IP and Port to run Agent Manager
    #needs to modify
    logging.info("HTTP server started on 0.0.0.0:" + str(port))
    uvicorn.run(app, host="0.0.0.0", port= port)

'''
obj_IP : ip of object detection service
obj_Port : port of object detection service
obj_Freq : sending freqency of object detection service
ges_IP : ip of gesture detection service
ges_Port : port of gesture detection service
ges_Freq : sending freqency of gesture detection service
newport : a agent port that is already created, no need to create again
newwebsocketport : a agent websocket port that is already created, no need to create again
'''
def create_agent(obj_IP="0", obj_Port=0, obj_Freq=0, ges_IP="0", ges_Port=0, ges_Freq=0, newport=0, newwebsocketport=0):
    #optional args old informations


    #get a unique agent information
    if newport == 0 or newwebsocketport == 0:
        newport, newwebsocketport = generate_agent_information()

    #connect to agent host and create agent by command
    ssh = paramiko.SSHClient()

    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    ssh.connect(hostname=Agent_Host, username=str(Agent_Host_ACCOUNT), password=str(Agent_Host_PASSWORD))

    logging.info(f"connected to Agent Host {Agent_Host}")

    #python Agent_websocket.py {IP} {Port} {websocket_port} {ip of obj det} {port of obj det} {sending freq of obj det} {ip of gesture det} {port of gesture det} {sending freq of gesture det}

    print("before command")
    #docker run -d --rm -p 8888:8888 -p 8889:8889 wlin90/agent_websocket:latest 0.0.0.0 8888 8889
    #print(f"nohup python3 Agent_websocket.py {Agent_Host} {newport} {newwebsocketport} > /dev/null 2>&1 &")
    #stdin, stdout, stderr = ssh.exec_command(f"nohup python3 Agent_websocket.py {Agent_Host} {port} {websocket_port} > /dev/null 2>&1 &")
    command = f"docker run -d --rm -v /home/logs:/app/logs -p {newport}:{newport} -p {newwebsocketport}:{newwebsocketport} wlin90/agent_websocket:1.0 {Agent_Host} {newport} {newwebsocketport}"
    command += f" {obj_IP} {obj_Port} {obj_Freq} {ges_IP} {ges_Port} {ges_Freq}"
    print(command)
    stdin, stdout, stderr = ssh.exec_command(command)
    print("after command")
    logging.info(f"executed command on Agent Host {command}")

    time.sleep(2)

    ssh.close()

    return Agent_Host, newport, newwebsocketport

#generate a new agent port and websocket port
def generate_agent_information():
    global port
    global websocket_port

    port += 1
    websocket_port += 1
    return port - 1, websocket_port - 1

def store_information(ar: str, agent: str, agentport: int, agentwebsocketport: int):
    logging.info(f"store info for AR: {ar} and agent: {agent} {agentport} {agentwebsocketport}")
    if os.path.exists('AR_Agent.json'):
        with open('AR_Agent.json', 'r') as json_file:
            data_list = json.load(json_file)
    else:
        data_list = []

    for data in data_list:
        if data["AR"] == ar:
            data_list.remove(data)

    newpair = {"AR": ar, "Agent": agent, "AgentPort": agentport, "AgentWebsocketPort": agentwebsocketport}

    data_list.append(newpair)

    with open('AR_Agent.json', 'w') as json_file:
        json.dump(data_list, json_file)

def find_pair_information(ar: str):
    logging.info(f"find the agent of AR ({ar})")
    if os.path.exists('AR_Agent.json'):
        with open('AR_Agent.json', 'r') as json_file:
            data_list = json.load(json_file)
    else:
        logging.error(f"Agent not found for {ar}")
        return None, None, None

    for data in data_list:
        if data["AR"] == ar:
            logging.info(f"Agent found, ip: {data['Agent']}, port: {data['AgentPort']}, websocketport: {data['AgentWebsocketPort']}")
            return data["Agent"], data["AgentPort"], data["AgentWebsocketPort"]
    logging.error(f"Agent not found for {ar}")
    return None, None, None

def subscribe_services(port: int, servicename: str):
    body = {
        "ip" : str(Agent_Host),
        "port" : port,
        "serviceType" : servicename
    }
    response = requests.post(f'http://{ControllerIP}:{ControllerPort}/subscribe', data = json.dumps(body))
    response = response.json()
    logging.info(f"subscribed {servicename} service for new agent: {response}")
    return response.get('IP'), response.get('Port'), response.get('Frequency')

# 客戶端連接後的處理
async def handle_client(websocket, path):
    print(f"Client connected from {path}")
    client_ip, client_port = websocket.remote_address
    logging.info(f"WebSocket Client connected from {client_ip}:{client_port}")
    print(f"client ip = {client_ip}, port = {client_port}")
    #todo
    #generate two port
    new_agent_port, new_agent_websocketport = generate_agent_information()
    print(f"got new agent info with port {new_agent_port} and websocketport {new_agent_websocketport}")
    logging.info(f"New agent generated for WebSocket client: Port={new_agent_port}, WebsocketPort={new_agent_websocketport}")
    #subscribe services for client from controller
    obj_ip, obj_port, obj_freq = subscribe_services(new_agent_port, "object")
    ges_ip, ges_port, ges_freq = subscribe_services(new_agent_port, "gesture")
    print("subscribed service")

    #create a agent by the information from controller, need to add gesture
    create_agent(obj_IP=obj_ip, obj_Port= obj_port, obj_Freq=obj_freq, ges_IP=ges_ip, ges_Port=ges_port, ges_Freq=ges_freq, newport=new_agent_port, newwebsocketport=new_agent_websocketport)
    print("created agent")
    #store the pair information of client and agent
    store_information(client_ip, Agent_Host, new_agent_port, new_agent_websocketport)
    #return the agent information
    response_data = {
        "host": Agent_Host,
        "port": new_agent_port,
        "websocket_port": new_agent_websocketport
    }
    # 將數據轉換為 JSON 格式
    #response_json = json.dumps(response_data)

    await websocket.send(f"{Agent_Host} {new_agent_port} {new_agent_websocketport}")

    try:
        # 持續等待來自客戶端的消息
        async for message in websocket:
            print(f"Received message from client: {message}")

            # 回應給客戶端
            response = f"Server received your message: {message}"
            #await websocket.send(response)
    except websockets.ConnectionClosed:
        print("Client disconnected")

# 啟動 WebSocket 伺服器
async def start_server():
    server = await websockets.serve(handle_client, "0.0.0.0", websocket_port)
    print("WebSocket server started on ws://0.0.0.0:" + str(websocket_port))
    logging.info("WebSocket server started on ws://0.0.0.0:" + str(websocket_port))
    await server.wait_closed()

if __name__ == "__main__":
    app.debug = False
    #run_server()
    threading.Thread(target = run_server).start()

    asyncio.get_event_loop().run_until_complete(start_server())
    asyncio.get_event_loop().run_forever()