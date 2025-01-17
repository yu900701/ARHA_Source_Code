from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import json
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from starlette.responses import JSONResponse
import yaml
import time
import requests
import concurrent.futures
import logging
import sys
import asyncio

GPU_MEMORY_LABEL = "nvidia.com/gpu.memory"
IN_CLUSTER = True
SERVICE_FILE = './information/service.json'
SERVICESPEC_FILE = './information/serviceSpec.json'
SUBSCRIPTION_FILE = './information/subscription.json'
NODE_STATUS_FILE = './information/nodestatus.json'
LOG_FILE = './logdir/controller.log'

# 設定 logging，將所有日誌寫入到 LOG_FILE
logging.basicConfig(
    filename= LOG_FILE,
    format='%(asctime)s %(levelname)s: %(message)s',
    level=logging.INFO
)

def lifespan(app: FastAPI):
    # 初始化 NODE_STATUS_FILE
    config.load_incluster_config() if IN_CLUSTER else config.load_kube_config()
    core_api = client.CoreV1Api()
    node_status_list = []

    # 獲取所有節點的標籤
    nodes = core_api.list_node().items
    for node in nodes:
        labels = node.metadata.labels
        if labels.get('arha-node-type') == 'computing-node':
            node_status_list.append(node.metadata.name)

    node_health_status = {}
    for node in node_status_list:
        ip = get_node_ip(node)
        if ip != "Error":
            try:
                if curl_health_check(ip).strip().lower() == 'ok':
                    node_health_status[node] = "healthy"
                else:
                    node_health_status[node] = "unhealthy"
            except Exception as e:
                node_health_status[node] = "unhealthy"
        else:
            node_health_status[node] = "unhealthy"

    with open(NODE_STATUS_FILE, 'w') as node_status_file:
        json.dump(node_health_status, node_status_file, indent=4)
    yield 

app = FastAPI(lifespan=lifespan)

# 中間件，用來紀錄每個 API 呼叫的詳情
@app.middleware("http")
async def log_requests(request: Request, call_next):
    # 取得 API 名稱 (路徑)、請求內容、來源 IP
    api_name = request.url.path
    request_body = await request.body()
    client_ip = request.client.host  # 取得來源 IP
    request_log = request_body.decode("utf-8") if request_body else None
    logging.info(f"{api_name} receive request {request_log} from IP: {client_ip}")

    # 呼叫 API 並取得回應
    try:
        response = await call_next(request)
        response_body = b"".join([chunk async for chunk in response.body_iterator])
        status_code = response.status_code  # 取得狀態碼
        response = JSONResponse(content=json.loads(response_body), status_code=status_code)
    except Exception as e:
        status_code = 500
        response = JSONResponse(content={"error": str(e)}, status_code=status_code)
    
    # 組合 log 訊息
    log_message = {
        "api_name": api_name,
        "client_ip": client_ip,  # 新增來源 IP 記錄
        "request": request_body.decode("utf-8") if request_body else None,
        "response": response.body.decode("utf-8"),
        "status_code": status_code  # 新增狀態碼記錄
    }
    response_log = response.body.decode("utf-8")
    # 將 log 訊息寫入到日誌檔
    # logging.info(json.dumps(log_message))
    logging.info(f"{api_name} response {response_log} and status code: {status_code}")
    
    return response

class SubscriptionRequest(BaseModel):
    ip: str
    port: int
    serviceType: str

# 不進行autoscaling的例外
class notAutoscaling(Exception):
    pass

@app.post('/subscribe')
async def subscribe(request: Request, subscription: SubscriptionRequest):
    data = subscription
    agent_ip = data.ip
    agent_port = data.port
    serviceType = data.serviceType
    serviceNotFound = True

    if not agent_ip or not serviceType:
        raise HTTPException(status_code=400, detail="Invalid input")

    # 檢查請求中的serviceType是否存在
    try:
        with open(SERVICESPEC_FILE, 'r') as serviceSpec_jsonFile:
            try:
                serviceSpec_data = json.load(serviceSpec_jsonFile)
                for serviceSpec in serviceSpec_data:
                    if serviceSpec['serviceType'] == serviceType:
                        serviceNotFound = False
                        break
                if (serviceNotFound):
                    raise HTTPException(status_code=500, detail="Service not in serviceSpec file")
            except json.decoder.JSONDecodeError: 
                raise HTTPException(status_code=500, detail="ServiceSpec file is empty")
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="ServiceSpec file not found")

    # 更新節點當前狀態
    try:
        with open(NODE_STATUS_FILE, 'r') as node_status_jsonFile:
            try:
                node_status_data = json.load(node_status_jsonFile)
                node_status_list = list(node_status_data.keys()) 
            except json.decoder.JSONDecodeError: 
                node_status_list = []
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Node_Status file not found")

    node_status_sync(node_status_list)

    subscription_result = subscribe_procedure(agent_ip, agent_port, serviceType, "null")

    if type(subscription_result) == str:
        print(subscription_result)
        return subscription_result
    else:
        print(subscription_result['message'])
        return {
            "IP": subscription_result['IP'],
            "Port": subscription_result['Port'],
            "Frequency": subscription_result['frequency']
        }

@app.post('/alert')
async def alert(request: Request):

    data = await request.json()
    alertType = data['alertType']
    alertContent = data['alertContent']

    # 處理Computing Node 故障的Case
    if alertType == 'workernode_failure':
        
        failnodeName = alertContent['nodeName']

        # 將故障的Computing Node上的所有服務從資料中清除
        try:
            with open(SERVICE_FILE, 'r') as service_file:
                try:
                    service_data = json.load(service_file)
                except json.decoder.JSONDecodeError:
                    service_data = []
        except FileNotFoundError:
            raise HTTPException(status_code=500, detail="Service file not found")
        
        failed_services_data = [item for item in service_data if item.get('nodeName') == failnodeName]
        service_data = [item for item in service_data if item.get('nodeName') != failnodeName]

        try:
            with open(SERVICE_FILE, 'w') as service_file:
                json.dump(service_data, service_file, indent=4)
        except Exception as e:
            raise HTTPException(status_code=500, detail="Failed to write to service file")

        # 將有訂閱故障Computing Node上服務之Agent進行轉移

        # 打開訂閱資料
        try:
            with open(SUBSCRIPTION_FILE, 'r') as subscription_jsonFile:
                try:
                    subscription_data = json.load(subscription_jsonFile)
                except json.decoder.JSONDecodeError:
                    subscription_data = []
        except FileNotFoundError:
            raise HTTPException(status_code=500, detail="Subscription file not found")

        # 挑選出需要重新訂閱的項目以及刪除subscription中需要重新訂閱的項目
        resubscription_data = []
        new_subscription_data = []
        for subscription in subscription_data:
            if subscription['nodeName'] == failnodeName:
                resubscription_data.append(subscription)
            else:
                new_subscription_data.append(subscription)
        try:
            with open(SUBSCRIPTION_FILE, 'w') as subscription_file:
                json.dump(new_subscription_data, subscription_file, indent=4)
        except Exception as e:
            raise HTTPException(status_code=500, detail="Failed to write to subscription file")

        message = 'No agent need to resubscribe'
        # 更新節點當前狀態
        try:
            with open(NODE_STATUS_FILE, 'r') as node_status_jsonFile:
                try:
                    node_status_data = json.load(node_status_jsonFile)
                    node_status_list = list(node_status_data.keys()) 
                except json.decoder.JSONDecodeError: 
                    node_status_list = []
        except FileNotFoundError:
            raise HTTPException(status_code=500, detail="Node_Status file not found")

        node_status_sync(node_status_list)

        # 將需要重新訂閱的agent進行重新訂閱
        rejectPortSet = set()
        for resubscription in resubscription_data:
            if resubscription['agentPort']  in rejectPortSet:
                continue
            resubscription_result = subscribe_procedure(resubscription['agentIP'], resubscription['agentPort'], resubscription['serviceType'], failnodeName)
            
            if type(resubscription_result) == str:
                if resubscription_result == 'reject the subscription':
                    rejectPortSet.add(resubscription['agentPort'])
                print(resubscription_result)
            elif resubscription['agentPort'] not in rejectPortSet:
                message = resubscription_result['message']
                print(message)
                if str(resubscription['serviceType']) == 'pose':
                    continue
                body = {
                    "servicename" : str(resubscription['serviceType']),
                    "ip" : str(resubscription_result['IP']),
                    "port" : int(resubscription_result['Port']),
                    "frequency" : float(resubscription_result['frequency'])
                }
                agent_status_code, agent_response = communicate_with_agent(body,str(resubscription['agentIP']), int(resubscription['agentPort']))
                print(agent_response)

        # 要把整個訂閱資料都重新處理(好像不用做了)
         
        # 把所有服務從集群中刪除掉
        for failed_services in failed_services_data:
            delete_pod(str(failed_services['serviceType'])+'-'+str(failed_services['nodeName'])+'-'+str(failed_services['hostPort']),'default')    
    return {"message": "Alert handled successfully" + message}

@app.post('/unsubscribe')
async def unsubscribe(request: Request):
    data = await request.json()
    agent_ip = request.client.host
    agent_port = data['port']
    message = "agent not found"

    try:
        with open(SUBSCRIPTION_FILE, 'r') as subscription_jsonFile:
            try:
                subscription_data = json.load(subscription_jsonFile)
            except json.decoder.JSONDecodeError:
                subscription_data = []
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail= "Subscription file not found")
    
    new_subscription_data = []
    podip_set = set()

    for subscription in subscription_data:
        if str(subscription['agentIP']) == str(agent_ip) and int(subscription['agentPort']) == int(agent_port):
            podip_set.add(subscription['podIP'])
            message = "unsubscribe successfully"
        else:
            new_subscription_data.append(subscription)

    try:
        with open(SUBSCRIPTION_FILE, 'w') as subscription_file:
            json.dump(new_subscription_data, subscription_file, indent=4)
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to write to subscription file")
    
    try:
        with open(SERVICE_FILE, 'r') as service_jsonFile:
            try:
                service_data = json.load(service_jsonFile)
            except json.decoder.JSONDecodeError: # 要是當前集群中沒有任何Pod
                raise HTTPException(status_code=404, detail= "Service file is empty")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail= "Service file not found")
    
    for service in service_data:
        # 更新服務當前的連線數
        if service['podIP'] in podip_set:
            service['currentConnection'] -=1 

    with open(SERVICE_FILE, 'w') as service_jsonFile:
        json.dump(service_data, service_jsonFile, indent=4)

    return_message = adjust_frequency()
    return {'message' : message + 'and' + return_message}

@app.post('/agentfail')
async def agentfail(request: Request):
    data = await request.json()
    old_agent_ip = data['old_ip']
    old_agent_port = data['old_port']
    new_agent_ip = data['new_ip']
    new_agent_port = data['new_port']
    subscribed_service_list = []
    podip_set = set()

    try:
        with open(SUBSCRIPTION_FILE, 'r') as subscription_jsonFile:
            try:
                subscription_data = json.load(subscription_jsonFile)
            except json.decoder.JSONDecodeError:
                raise HTTPException(status_code=404, detail= "Subscription file is empty")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail= "Subscription file not found")

    for subscription in subscription_data:
        if str(subscription['agentIP']) == old_agent_ip and int(subscription['agentPort']) == old_agent_port:
            subscription['agentIP'] = new_agent_ip
            subscription['agentPort'] = new_agent_port
            podip_set.add(str(subscription['podIP']))

    try:
        with open(SUBSCRIPTION_FILE, 'w') as subscription_file:
            json.dump(subscription_data, subscription_file, indent=4)
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to write to subscription file")

    try:
        with open(SERVICE_FILE, 'r') as service_jsonFile:
            try:
                service_data = json.load(service_jsonFile)
            except json.decoder.JSONDecodeError: 
                raise HTTPException(status_code=404, detail= "Service file is empty")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail= "Service file not found")

    for service in service_data:
        if service['podIP'] in podip_set:
            subscribed_service_list.append(
                {
                    "ServiceType" : str(service['serviceType']),
                    "IP" : str(service['hostIP']),
                    "Port" : int(service['hostPort']),
                    "Frequency" : float(service['currentFrequency'])
                }
            )

    return subscribed_service_list

@app.post('/deletepod')
async def deletepod(request: Request):
    data = await request.json()
    node_name = data['nodeName']
    service_type = data['serviceType']
    host_port = data['hostPort']
    serviceNumTotal = 0
    serviceWorkAbility = {}

    try:
        with open(SERVICESPEC_FILE, 'r') as serviceSpec_jsonFile:
            try:
                serviceSpec_data = json.load(serviceSpec_jsonFile)
                for serviceSpec in serviceSpec_data:
                   serviceWorkAbility[serviceSpec['serviceType']] = int(serviceSpec['workAbility'][node_name]) 
            except json.decoder.JSONDecodeError: 
                return "ServiceSpec file is empty"
    except FileNotFoundError:
        return "ServiceSpec file not found"
    
    try:
        with open(SERVICE_FILE, 'r') as service_jsonFile:
            try:
                service_data = json.load(service_jsonFile)
            except json.decoder.JSONDecodeError: # 要是當前集群中沒有任何Pod
                service_data = []
    except FileNotFoundError:
        return "Service file not found"
    
    new_service_data = []

    # 還要統計節點上的服務數量
    for service in service_data:
        if str(service['serviceType']) == str(service_type) and str(service['nodeName']) == str(node_name) and int(service['hostPort']) == int(host_port):
            delete_pod(str(service['serviceType'])+'-'+str(service['nodeName'])+'-'+str(service['hostPort']),'default')
            serviceNumTotal-=1
        else:
            new_service_data.append(service)
        if str(service['nodeName']) == str(node_name): serviceNumTotal+=1

    # 在加入新資料前，將workloadlimit更新
    for service in new_service_data:
        if str(service['nodeName']) == str(node_name): service['workloadLimit'] = serviceWorkAbility[service['serviceType']] / serviceNumTotal

    with open(SERVICE_FILE, 'w') as service_jsonFile:
        json.dump(new_service_data, service_jsonFile, indent=4)
    
    # 在工作負載增加後嘗試提高頻率
    return_message = adjust_frequency()

    return {'message' : 'Pod delete successfully ' + return_message}

@app.post('/noderecovery')
async def noderecovery(request: Request):
    data = await request.json()
    node_name = str(data['nodeName'])
    isFrequencyDown = False
    usedPort = set()
    service_data_on_node = {}
    serviceTypeNumTotal = 0
    serviceDeployedNum = 0
    pod_name_list = []

    try:
        with open(SERVICE_FILE, 'r') as service_jsonFile:
            try:
                service_data = json.load(service_jsonFile)
            except json.decoder.JSONDecodeError: # 要是當前集群中沒有任何Pod
                service_data = []
    except FileNotFoundError:
        return "Service file not found"
    
    try:
        with open(SERVICESPEC_FILE, 'r') as serviceSpec_jsonFile:
            try:
                serviceSpec_data = json.load(serviceSpec_jsonFile)
                for serviceSpec in serviceSpec_data:
                    service_data_on_node[serviceSpec['serviceType']] = {'workloadLimit': serviceSpec['workAbility'][node_name], 
                                                                        'gpuMemoryRequest': serviceSpec['gpuMemoryRequest'],
                                                                        'maxServiceNum': serviceSpec['workAbility'][node_name]/serviceSpec['frequencyLimit'][0]
                                                                        }
                    serviceTypeNumTotal += 1
                    frequencyLimit = serviceSpec['frequencyLimit']
            except json.decoder.JSONDecodeError: 
                return "ServiceSpec file is empty"
    except FileNotFoundError:
        return "ServiceSpec file not found"
    
    for service in service_data:
        usedPort.add(service['hostPort'])
        currentFrequency = float(service['currentFrequency'])
        if currentFrequency < int(service['frequencyLimit'][0]):
            isFrequencyDown = True

    sorted_service_data_on_node = dict(sorted(service_data_on_node.items(), key=lambda item: item[1]['workloadLimit']))

    config.load_incluster_config() if IN_CLUSTER else config.load_kube_config()
    core_api = client.CoreV1Api()
    node = core_api.read_node(name=node_name)
    label_value = node.metadata.labels.get(GPU_MEMORY_LABEL)
    counter = 0

    while label_value is None:
        node = core_api.read_node(name=node_name)
        label_value = node.metadata.labels.get(GPU_MEMORY_LABEL)
        if counter == 12:
            logging.info(f"can't read node label")
            break
        counter += 1
        await asyncio.sleep(5)

    label_value = int(label_value) # 這裡才轉換型態是因為避免對None type進行轉換型態的BUG

    for (key, value) in sorted_service_data_on_node.items():
        if label_value >= value['gpuMemoryRequest'] * 1024 and serviceDeployedNum+1 <= value['maxServiceNum']:
            label_value -= value['gpuMemoryRequest'] * 1024
            for i in range(30500, 31000):
                if i not in usedPort:
                    hostPort = i
                    usedPort.add(i)
                    break
            resp = deploy_pod(str(key),hostPort, node_name)
            logging.info(f"Function deploy_pod finish")
            serviceDeployedNum += 1
            podIP = resp.status.pod_ip
            hostIP = resp.status.host_ip
            service_data.append({
                "podIP" : str(podIP),
                "hostPort" : int(hostPort),
                "serviceType" : str(key),
                "currentConnection" : 0,
                "nodeName" : node_name,
                "hostIP" : str(hostIP),
                "frequencyLimit" : frequencyLimit,
                "currentFrequency" : currentFrequency,
                "workloadLimit" : value['workloadLimit']
            })
            pod_name_list.append(str(key)+'-'+ node_name +'-'+str(hostPort))

    for service in service_data:
        if service['nodeName'] == node_name:
            service['workloadLimit'] = service['workloadLimit']/serviceDeployedNum

    with open(SERVICE_FILE, 'w') as service_file:
        json.dump(service_data, service_file, indent=4) 

    # 更新節點當前狀態
    try:
        with open(NODE_STATUS_FILE, 'r') as node_status_jsonFile:
            try:
                node_status_data = json.load(node_status_jsonFile)
                for (key, value) in node_status_data.items():
                    if key == node_name:
                        value = 'healthy'
                        break
            except json.decoder.JSONDecodeError: 
                node_status_list = []
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Node_Status file not found")    

    try:
        with open(NODE_STATUS_FILE, 'w') as node_status_file:
            json.dump(node_status_data, node_status_file, indent=4)
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to write to node_status file")

    # 在前面先蒐集所有要檢查的Pod名字，這邊就一次檢查完畢
    for pod_name in pod_name_list:
        counter = 0
        is_pod_ready = False
        while not is_pod_ready:
            pod = core_api.read_namespaced_pod(name=pod_name, namespace='default')
            for data in pod.status.conditions:
                if data.type == 'Ready' and data.status == 'True':
                    is_pod_ready = True
            if counter == 12:
                logging.info(f"{pod_name} is still not Ready after 1 minutes")
                break
            if not is_pod_ready:
                await asyncio.sleep(5)
                counter += 1
        logging.info(f"Pod {pod_name} is Ready")

    if isFrequencyDown:
        adjust_frequency()
        return {'message' : 'node recovery successfully and call FrequencyAjustment()'}
    else:
        return {'message' : 'node recovery successfully'}

@app.post('/deploypod')
async def deploypod(request: Request):
    data = await request.json()
    node_name = str(data['nodeName'])
    hostPort = int(data['hostPort'])
    service_type = str(data['service_type'])
    serviceamountonnode = int(data['amount'])
    resp = deploy_pod(service_type,hostPort, node_name)

    try:
        with open(SERVICE_FILE, 'r') as service_jsonFile:
            try:
                service_data = json.load(service_jsonFile)
            except json.decoder.JSONDecodeError: # 要是當前集群中沒有任何Pod
                service_data = []
    except FileNotFoundError:
        return "Service file not found"

    if  service_type == 'object':
        workloadLimit = 15
    else:
        if node_name == 'workergpu':
            workloadLimit = 120
        else:
            workloadLimit = 160

    service_data.append({
        "podIP" : str(resp.status.pod_ip),
        "hostPort" : int(hostPort),
        "serviceType" : service_type,
        "currentConnection" : 0,
        "nodeName" : str(node_name),
        "hostIP" : str(resp.status.host_ip),
        "frequencyLimit" : [5,3],
        "currentFrequency" : 5,
        "workloadLimit" : workloadLimit/serviceamountonnode        
    })

    with open(SERVICE_FILE, 'w') as service_file:
        json.dump(service_data, service_file, indent=4)     

    return 'deploy finish'

def deploy_pod(service_type,hostPort, node_name): #之後應該要在controller API中補一個可以直接部屬Pod的功能
    try:
        config.load_incluster_config() if IN_CLUSTER else config.load_kube_config()
    except Exception as e:
        print(f"Error loading kubeconfig: {e}")
        logging.error(f"Error loading kubeconfig: {e}")
        raise

    core_api = client.CoreV1Api()

    # 讀取要部署的Pod的YAML文件
    try:
        with open(f"service_yaml/{service_type}.yaml") as f:
            dep = yaml.safe_load(f)
    except Exception as e:
        print("Service Type YAML file not found")
        logging.error("Service Type YAML file not found")
        raise
    # 生成唯一的Pod名稱
    unique_name = f"{service_type}-{str(node_name)}-{str(hostPort)}"

    # 更新Pod名稱和hostPort
    dep['metadata']['name'] = unique_name
    dep['spec']['containers'][0]['ports'][0]['hostPort'] = hostPort

    # 設置nodeSelector以指定部署節點
    dep['spec']['nodeSelector'] = {'kubernetes.io/hostname': node_name}

    # 部署Pod
    try:
        resp = core_api.create_namespaced_pod(body=dep, namespace='default')
        print(f"Pod {resp.metadata.name} created.")
        logging.info(f"Send the request of deploying Pod {resp.metadata.name}.")
    except ApiException as e:
        print(f"Exception when deploying Pod: {e}")
        logging.error(f"Exception when deploying Pod: {e}")
        raise

    while True:
        resp = core_api.read_namespaced_pod(name=unique_name, namespace='default')
        if resp.spec.node_name and resp.status.pod_ip and resp.status.host_ip:
            print(f"Pod {unique_name} (IP:{resp.status.pod_ip}) is scheduled to node {resp.spec.node_name} (IP:{resp.status.host_ip}).")
            break
        time.sleep(0.5)  # 等待0.5秒再檢查一次
    return resp    

def delete_pod(pod_name, namespace='default'):

    try:
        config.load_incluster_config() if IN_CLUSTER else config.load_kube_config()
    except Exception as e:
        print(f"Error loading kubeconfig: {e}")
        logging.error(f"Error loading kubeconfig: {e}")
        raise

    # 建立 API 客戶端
    core_api = client.CoreV1Api()

    try:
        # 刪除指定 namespace 中的 Pod
        core_api.delete_namespaced_pod(name=pod_name, namespace=namespace)
        print(f"Pod {pod_name} deleted successfully in namespace {namespace}.")
    except ApiException as e:
        if e.status == 404:
            print(f"Pod {pod_name} not found in namespace {namespace}.")
        else:
            print(f"Failed to delete Pod: {e}")

def get_pod_phase(pod_name: str, namespace: str) -> str:
    try:
        config.load_incluster_config() if IN_CLUSTER else config.load_kube_config()
    except Exception as e:
        print(f"Error loading kubeconfig: {e}")
        raise
    core_api = client.CoreV1Api()

    try:
        # 獲取 Pod 資訊
        pod = core_api.read_namespaced_pod(name=pod_name, namespace=namespace)
        # 返回 Pod 的 phase
        return pod.status.phase
    except client.exceptions.ApiException as e:
        print(f"Exception when calling CoreV1Api->read_namespaced_pod: {e}")
        return "Unknown"
    
def curl_health_check(ip: str):
    url = f"http://{ip}:10248/healthz"
    try:
        # 發送 GET 請求，設置超時為 1 秒
        response = requests.get(url, timeout=1)
        
        # 檢查回應狀態碼是否為 200 (OK)
        if response.status_code == 200:
            print(f"Health check successful for {url}")
            print("Response Status Code:", response.status_code)
            print("Response Body:", response.text)
            return response.text
        else:
            return f"Health check failed for {url}. Status Code: {response.status_code}"
    
    except requests.exceptions.Timeout:
        return f"Request to {url} timed out. The URL may not exist."
    except requests.exceptions.RequestException as e:
        return f"An error occurred while trying to reach {url}: {e}"
    
def subscribe_procedure(agentIP, agentPort, serviceType, notDeployedNodeName):
    counter = 0
    agent_ip = agentIP
    agent_port = agentPort
    serviceType = serviceType
    usedPort = []
    nodeisHealthy = True

    # 打開Service列表
    try:
        with open(SERVICE_FILE, 'r') as service_jsonFile:
            try:
                service_data = json.load(service_jsonFile)
            except json.decoder.JSONDecodeError: # 要是當前集群中沒有任何Pod
                service_data = []
    except FileNotFoundError:
        return "Service file not found"

    # 打開節點狀態列表
    try:
        with open(NODE_STATUS_FILE, 'r') as node_status_jsonFile:
            try:
                node_status_data = json.load(node_status_jsonFile)
            except json.decoder.JSONDecodeError: 
                node_status_data = []
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Node_Status file not found")
    
    if(notDeployedNodeName != "null"):
        node_status_data[notDeployedNodeName] = 'unhealthy'

    # find the pod to subscribe
    selectedPodIndex = -1
    for service in service_data:
        if service['serviceType'] == serviceType:
            if(((int(service['currentConnection'])+1)*int(service['currentFrequency'])) <= service['workloadLimit']):
                if(node_status_data[str(service['nodeName'])] == 'healthy'):
                    nodeisHealthy = True
                else:
                    nodeisHealthy = False

                if(get_pod_phase(str(service['serviceType'])+'-'+str(service['nodeName'])+'-'+str(service['hostPort']),'default') == "Running" and nodeisHealthy):                    
                    if(selectedPodIndex == -1):
                        selectedPodIndex = counter
                        leastConnection = int(service['currentConnection'])
                    elif(int(service['currentConnection']) < leastConnection):
                        selectedPodIndex = counter
                        leastConnection = int(service['currentConnection'])
        counter += 1
        
    # check if there is selected_pod
    if selectedPodIndex == -1: # 這裡代表當前pod資源不足的case，要先進行auto-scaling
        try:
            # auto-scaling流程
             
            nodeDeployedList = []
            serviceNum = {}
            serviceCapability = {}
            deployOnTheNode = True
            serviceWorkloadLimit = {}

            # 查看集群中有哪些運算節點及服務類型
            try:
                with open(SERVICESPEC_FILE, 'r') as serviceSpec_jsonFile:
                    try:
                        serviceSpec_data = json.load(serviceSpec_jsonFile)
                        for serviceSpec in serviceSpec_data:
                            serviceNum[serviceSpec['serviceType']] = 0
                            nodeDeployedList.extend(serviceSpec["workAbility"].keys())
                        nodeDeployedList = list(set(nodeDeployedList))
                    except json.decoder.JSONDecodeError: 
                        return "ServiceSpec file is empty"
            except FileNotFoundError:
                return "ServiceSpec file not found"

            # 打開Service列表
            try:
                with open(SERVICE_FILE, 'r') as service_jsonFile:
                    try:
                        service_data = json.load(service_jsonFile)
                    except json.decoder.JSONDecodeError: # 要是當前集群中沒有任何Pod
                        service_data = []
            except FileNotFoundError:
                return "Service file not found"
            
            """
            檢查在各個節點上部署服務後是否能滿足以下條件
            1. VRAM 足夠
            2. 服務獲得的運算資源能滿足一般狀況下的QoS
            3. 節點上沒有運行我們要部署的服務
            4. 節點上其他服務能負擔原有的工作量
            """

            config.load_incluster_config() if IN_CLUSTER else config.load_kube_config()
            core_api = client.CoreV1Api()
            
            nodeDeployedListforLoop = nodeDeployedList[:]

            for nodeDeployed in nodeDeployedListforLoop:

                # 檢查節點狀態
                if(node_status_data[nodeDeployed] != "healthy"):
                    nodeDeployedList.remove(nodeDeployed)
                    continue

                # 將節點中所有類型的服務數量歸0
                for key in serviceNum:
                    serviceNum[key] = 0
                gpuMemoryRequest = 0
                serviceNumTotal = 0
                deployOnTheNode = True
                node = core_api.read_node(name=nodeDeployed)
                label_value = int(node.metadata.labels.get(GPU_MEMORY_LABEL))

                # 統計節點中各類型服務的數量
                for service in service_data:
                    if service['nodeName'] == nodeDeployed:
                        serviceNum[service['serviceType']] += 1
                        serviceNumTotal += 1

                # 預先將要部署的服務統計進去
                serviceNum[serviceType] += 1
                serviceNumTotal += 1

                # 計算服務部署後節點需要多少VRAM以及節點上所有服務獲得的運算資源能否滿足一般狀況下的QoS
                for serviceSpec in serviceSpec_data:
                    numOfSerivceOnNode = serviceNum[serviceSpec['serviceType']]
                    workAbility = int(serviceSpec['workAbility'][nodeDeployed])
                    serviceWorkloadLimit[serviceSpec['serviceType']] = serviceSpec['workAbility']
                    standardQoS = int(serviceSpec['frequencyLimit'][0])
                    gpuMemoryRequest += (serviceSpec['gpuMemoryRequest'] * numOfSerivceOnNode * 1024)
                    if numOfSerivceOnNode != 0 and workAbility/serviceNumTotal < standardQoS:
                        deployOnTheNode = False
                    if serviceSpec['serviceType'] == serviceType:
                        serviceCapability[nodeDeployed] = workAbility/serviceNumTotal

                for service in service_data:
                    if service['nodeName'] == nodeDeployed:
                        # 檢查節點上是否有運行我們想要部署的服務
                        if service['serviceType'] == serviceType:
                            deployOnTheNode = False
                            continue
                        # 計算服務部署後，節點上其他服務是否能負擔原有的工作量 
                        if serviceWorkloadLimit[service['serviceType']][nodeDeployed] / serviceNumTotal < int(service['currentConnection']) * int(service['currentFrequency']):
                            deployOnTheNode = False
                            continue
                           
                if not deployOnTheNode:
                    nodeDeployedList.remove(nodeDeployed)
                    del serviceCapability[nodeDeployed]
                    continue

                if label_value < gpuMemoryRequest:
                    nodeDeployedList.remove(nodeDeployed)
                    del serviceCapability[nodeDeployed]
                    continue

            # 檢查nodeDeployedList是否為空，空的話代表運算資源不足，要調降頻率
            if len(nodeDeployedList) == 0:
                raise notAutoscaling
            
            NodeSelectedIndex = 0
            counter = 0
            maxServiceCapability = 0
            # 從nodeDeployedList中選出運算資源最充沛的節點
            for nodeDeployed in nodeDeployedList:
                if serviceCapability[nodeDeployed] > maxServiceCapability:
                    NodeSelectedIndex = counter
                    maxServiceCapability = int(serviceCapability[nodeDeployed])
                counter += 1

            selectedNodeName = nodeDeployedList[NodeSelectedIndex]
            serviceNumTotal = 1

            if service_data == []:
                hostPort = 30500
            else:
                for service in service_data:
                    usedPort.append(service['hostPort'])
                    if service['serviceType'] == serviceType:
                        frequency = service['currentFrequency']
                    if service['nodeName'] == selectedNodeName:
                        serviceNumTotal += 1
                # 決定hostport number        
                for i in range(30500,31000):
                    Portisused = False
                    for usedport in usedPort:
                        if usedport == i:
                            Portisused = True
                            break
                    if(not Portisused):
                        hostPort = i
                        break 

            resp = deploy_pod(serviceType,hostPort, selectedNodeName)
            logging.info(f"Function deploy_pod finish")

            podIP = resp.status.pod_ip
            hostIP = resp.status.host_ip
            nodeName = resp.spec.node_name

            # 抓取該serviceType的頻率上下限以及在該節點的最大運算量
            for serviceSpec in serviceSpec_data:
                if serviceSpec['serviceType'] == serviceType:
                    frequencyLimit = serviceSpec['frequencyLimit']
                    workloadLimit = serviceSpec['workAbility'][str(nodeName)]

            # 把節點上所有服務的工作量進行調整 
            for service in service_data:
                if service['nodeName'] == selectedNodeName:
                    service['workloadLimit'] = serviceWorkloadLimit[service['serviceType']][selectedNodeName] / serviceNumTotal
            # ToDo 要補算當頻率是被調降過的話要重算最高頻率的程式 
            # if frequency != frequencyLimit[0]:
            frequency = frequencyLimit[0]
            # 把新的 Service Instance 資訊存入service.json中
            service_data.append({
                "podIP" : str(podIP),
                "hostPort" : int(hostPort),
                "serviceType" : serviceType,
                "currentConnection" : 0,
                "nodeName" : str(nodeName),
                "hostIP" : str(hostIP),
                "frequencyLimit" : frequencyLimit,
                "currentFrequency" : frequency,
                "workloadLimit" : workloadLimit/serviceNumTotal
            })

            selectedPodIndex = len(service_data) - 1

            print(f"New Pod deployed. PodIP: {podIP}, NodeName: {nodeName}")
        except notAutoscaling: 
            maxFrequency = 0
            freqcount = 0
            selectedfreqIndex = -1

            # 打開Service列表
            try:
                with open(SERVICE_FILE, 'r') as service_jsonFile:
                    try:
                        service_data = json.load(service_jsonFile)
                    except json.decoder.JSONDecodeError: # 要是當前集群中沒有任何Pod
                        service_data = []
            except FileNotFoundError:
                return "Service file not found"
            
            # 計算頻率最高能調降至多少
            for service in service_data:
                if service['serviceType'] == serviceType:
                    if(get_pod_phase(str(service['serviceType'])+'-'+str(service['nodeName'])+'-'+str(service['hostPort']),'default') == "Running" and node_status_data[str(service['nodeName'])] == 'healthy'):
                        workloadLimit = service['workloadLimit']
                        currentConnection = int(service['currentConnection'])
                        if(maxFrequency < workloadLimit/(currentConnection+1)):
                            maxFrequency = workloadLimit/(currentConnection+1)
                            selectedfreqIndex = freqcount
                freqcount = freqcount + 1
            
            # 打開ServiceSpec列表
            try:
                with open(SERVICESPEC_FILE, 'r') as serviceSpec_jsonFile:
                    try:
                        serviceSpec_data = json.load(serviceSpec_jsonFile)
                        for serviceSpec in serviceSpec_data:
                            if serviceSpec['serviceType'] == serviceType:
                                serviceNotFound = False
                                freq_lowerLimit = serviceSpec['frequencyLimit'][1]
                                break
                        if (serviceNotFound):
                            return "Service not in serviceSpec file"
                    except json.decoder.JSONDecodeError: 
                        return "ServiceSpec file is empty"
            except FileNotFoundError:
                return "ServiceSpec file not found"
            
            # 檢查調降後的頻率是否低於使用者最低規格需求
            if(maxFrequency >= freq_lowerLimit):
                for service in service_data:
                    service['currentFrequency'] = maxFrequency
                # 打開訂閱資料
                try:
                    with open(SUBSCRIPTION_FILE, 'r') as subscription_jsonFile:
                        try:
                            subscription_data = json.load(subscription_jsonFile)
                        except json.decoder.JSONDecodeError:
                            subscription_data = []
                except FileNotFoundError:
                    return "Subscription file not found"

                # 調降所有Agent的頻率
                for subscription in subscription_data:
                    if str(subscription['serviceType']) != 'pose':
                        body= {
                            "servicename": str(subscription['serviceType']),
                            "ip": 'null',
                            "port": 0,
                            "frequency": maxFrequency
                        }
                        communicate_with_agent(body,str(subscription['agentIP']), int(subscription['agentPort']))
                      
                # 新增新的訂閱資訊至subscription_data
                subscription_data.append({
                    "agentIP" : agent_ip,
                    "agentPort" : agent_port,
                    "podIP" : service_data[selectedfreqIndex]['podIP'],
                    "serviceType" : service_data[selectedfreqIndex]['serviceType'],
                    "nodeName" : service_data[selectedfreqIndex]['nodeName']
                })

                # 更新subscription.json的內容
                with open(SUBSCRIPTION_FILE, 'w') as subscription_jsonFile:
                    json.dump(subscription_data, subscription_jsonFile, indent=4)

                service_data[selectedfreqIndex]['currentConnection'] += 1
                # 更新service.json的內容
                with open(SERVICE_FILE, 'w') as service_jsonFile:
                    json.dump(service_data, service_jsonFile, indent=4)
                return {
                    "message": "frequency downgrade",
                    "IP": service_data[selectedfreqIndex]['hostIP'], 
                    "Port":service_data[selectedfreqIndex]['hostPort'], 
                    "frequency": maxFrequency
                    } # 這裡回傳這個是有調降頻率的case，全部的訂閱者都要發送訊息                                   
            else:
                return  "reject the subscription"
    else:
        podIP = service_data[selectedPodIndex]['podIP']
        hostPort = service_data[selectedPodIndex]['hostPort']
        nodeName = service_data[selectedPodIndex]['nodeName']
        frequency = service_data[selectedPodIndex]['currentFrequency']

    # update the subscription data
    subscribe_relation = {
        "agentIP": agent_ip,
        "agentPort": agent_port,
        "podIP": podIP,
        "serviceType": serviceType,
        "nodeName": nodeName
    }

    try:
        with open(SUBSCRIPTION_FILE, 'r') as subscription_jsonFile:
            try:
                subscription_data = json.load(subscription_jsonFile)
            except json.decoder.JSONDecodeError:
                subscription_data = []
    except FileNotFoundError:
        subscription_data = []

    subscription_data.append(subscribe_relation)

    with open(SUBSCRIPTION_FILE, 'w') as subscription_jsonFile:
        json.dump(subscription_data, subscription_jsonFile, indent=4)

    service_data[selectedPodIndex]['currentConnection'] += 1
    with open(SERVICE_FILE, 'w') as service_jsonFile:
        json.dump(service_data, service_jsonFile, indent=4)

    # 這裡要回傳訂閱成功，以及要回傳給送出訂閱請求的agent的相關資訊
    return {
        "message": "subscription successful",
        "IP":service_data[selectedPodIndex]['hostIP'], 
        "Port":hostPort, 
        "frequency": frequency
        }

def get_node_ip(node_name: str) -> str:
    try:
        config.load_incluster_config() if IN_CLUSTER else config.load_kube_config()
    except Exception as e:
        print(f"Error loading kubeconfig: {e}")
        raise

    core_api = client.CoreV1Api()

    try:
        # 獲取指定節點的資訊
        node = core_api.read_node(name=node_name)
        
        # 提取節點的 IP 地址，通常是節點地址列表中的 InternalIP
        for address in node.status.addresses:
            if address.type == "InternalIP":
                return address.address
        
        # 如果沒有找到 InternalIP，就返回 "未找到" 的消息
        print("InternalIP not found")
        return "Error"
    
    except client.exceptions.ApiException as e:
        print(f"Exception when calling CoreV1Api->read_node: {e}")
        return "Error"

def node_status_sync(node_name_list: List[str]):
    node_health_status = {}

    # 使用 ThreadPoolExecutor 平行處理健康檢查
    with concurrent.futures.ThreadPoolExecutor() as executor:
        # 用於儲存未來結果的字典
        future_to_node = {}
        
        for node_name in node_name_list:
            # 呼叫 get_node_ip 取得節點 IP
            ip = get_node_ip(node_name)
            
            if ip != "Error":
                # 提交 curl_health_check 到執行緒池，並將 node_name 和 future 綁定
                future = executor.submit(curl_health_check, ip)
                future_to_node[future] = node_name
            else:
                # 如果未能取得 IP，視為 unhealthy
                node_health_status[node_name] = "unhealthy"

        # 收集所有已完成的健康檢查
        for future in concurrent.futures.as_completed(future_to_node):
            node_name = future_to_node[future]
            try:
                # 獲取健康檢查結果
                health_status = future.result()
                
                # 根據回傳值來決定健康狀態
                if health_status.strip().lower() == 'ok':
                    node_health_status[node_name] = "healthy"
                else:
                    node_health_status[node_name] = "unhealthy"
                    
            except Exception as e:
                # 捕捉任何執行過程中的例外情況
                node_health_status[node_name] = "unhealthy"
    try:
        with open(NODE_STATUS_FILE, 'w') as node_status_file:
            json.dump(node_health_status, node_status_file, indent=4)
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to write to node_status file")
    # 將結果轉換為 JSON 格式並返回
    return json.dumps(node_health_status, indent=4)

def communicate_with_agent(data: dict, agent_ip: str, agent_port: int):
    url = f"http://{agent_ip}:{agent_port}/servicechange"
    try:
        response = requests.post(url, data=json.dumps(data))
        logging.info(f"communicate with Agent {agent_ip} {agent_port}")
        return response.status_code, response.text
    except requests.exceptions.RequestException as e:
        return None, str(e)   

def adjust_frequency(): #該函式無法防範傳送頻率低於使用者規格的case
    subscriptionNumofEachService = {}
    IndexofEachService = {}
    podIPIndex_dict = {}

    try:
        with open(SERVICE_FILE, 'r') as service_file:
            try:
                service_data = json.load(service_file)
            except json.decoder.JSONDecodeError:
                service_data = []
    except FileNotFoundError:
        return "Service file not found"

    # 統計訂閱各類型服務的終端數量並統計各類型服務在service_data中的哪些index出現
    for index, service in enumerate(service_data):
        if service['serviceType'] not in subscriptionNumofEachService.keys():
            subscriptionNumofEachService[service['serviceType']] = 0
        subscriptionNumofEachService[service['serviceType']] += int(service['currentConnection'])
        service['currentConnection'] = 0
        if service['serviceType'] not in IndexofEachService.keys():
            IndexofEachService[service['serviceType']] = []
        IndexofEachService[service['serviceType']].append(index)
        minFrequency = service['frequencyLimit'][0]

    # 針對各類型服務計算如何分配終端的訂閱
    for (key, value) in subscriptionNumofEachService.items():
        IndexofThisService = IndexofEachService[key]
        # 計算如何分配訂閱該服務的終端
        for i in range(0, value):
            selectedPodIndex = -1
            leastConnection = sys.maxsize
            for index in IndexofThisService:
                if(((int(service_data[index]['currentConnection'])+1)*int(minFrequency)) <= service_data[index]['workloadLimit'] and service_data[index]['currentConnection']< leastConnection):
                    selectedPodIndex = index
                    leastConnection = service_data[index]['currentConnection']
            if selectedPodIndex == -1:
                temp_maxFrequency = 0
                for index in IndexofThisService:
                    if (temp_maxFrequency < service_data[index]['workloadLimit']/(service_data[index]['currentConnection']+1)):
                        temp_maxFrequency = service_data[index]['workloadLimit']/(service_data[index]['currentConnection']+1)
                        selectedPodIndex = index
                minFrequency = temp_maxFrequency
            service_data[selectedPodIndex]['currentConnection'] += 1

    # 將計算出來的頻率值存入service_data中
    for index, service in enumerate(service_data):
        service['currentFrequency'] = minFrequency
        podIPIndex_dict[service['podIP']] = {}
        podIPIndex_dict[service['podIP']]['index'] = index
        podIPIndex_dict[service['podIP']]['connection'] = service['currentConnection']

    try:
        with open(SUBSCRIPTION_FILE, 'r') as subscription_jsonFile:
            try:
                subscription_data = json.load(subscription_jsonFile)
            except json.decoder.JSONDecodeError:
                subscription_data = []
    except FileNotFoundError:
        return "Subscription file not found"

    # 根據分配結果調整終端的訂閱
    for subscription in subscription_data:

        # 如果該終端原本訂閱的Pod還有名額
        if podIPIndex_dict[subscription['podIP']]['connection'] != 0:
            podIPIndex_dict[subscription['podIP']]['connection'] -=1
            body = {
                'servicename': str(subscription['serviceType']),
                "ip": 'null',
                "port": 0,
                "frequency": minFrequency
            }
            communicate_with_agent(body,str(subscription['agentIP']), int(subscription['agentPort']))

        # 如果該終端原本訂閱的Pod沒有名額了
        else:
            IndexofEachService[subscription['serviceType']].remove(podIPIndex_dict[subscription['podIP']]['index'])
            for index in IndexofEachService[subscription['serviceType']]:
                if podIPIndex_dict[service_data[index]['podIP']]['connection'] != 0:
                    podIPIndex_dict[service_data[index]['podIP']]['connection'] -=1
                    body = {
                        'servicename' : str(subscription['serviceType']),
                        "ip": str(service_data[index]['hostIP']),
                        "port": int(service_data[index]['hostPort']),
                        "frequency": minFrequency
                    }
                    communicate_with_agent(body,str(subscription['agentIP']), int(subscription['agentPort']))
                    break
            subscription['podIP'] = service_data[index]['podIP']
            subscription['nodeName'] = service_data[index]['nodeName']
    
    try:
        with open(SUBSCRIPTION_FILE, 'w') as subscription_file:
            json.dump(subscription_data, subscription_file, indent=4)
    except Exception as e:
        return "Failed to write to subscription file"

    try:
        with open(SERVICE_FILE, 'w') as service_file:
            json.dump(service_data, service_file, indent=4)
    except Exception as e:
        return "Failed to write to service file"
        
    return f"adjust frequency to {minFrequency}"