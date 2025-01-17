import time
import requests
from kubernetes import client, config
import httpx
import asyncio
import logging
import json

LOG_FILE = './logdir/monitor.log'
IN_CLUSTER = True
PROMETHEUS_URL = 'http://prometheus-stack-kube-prom-prometheus.prometheus.svc.cluster.local:9090/api/v1/query'

if IN_CLUSTER:
    controller_alert_url = 'http://controller-service:80/alert'
else:
    controller_alert_url = "http://10.52.52.126:30004/alert"

# 設定 logging，將所有日誌寫入到 LOG_FILE
logging.basicConfig(
    filename= LOG_FILE,
    format='%(asctime)s %(levelname)s: %(message)s',
    level=logging.INFO
)

async def get_computing_nodes():
    try:
        config.load_incluster_config() if IN_CLUSTER else config.load_kube_config()
    except Exception as e:
        print(f"Error loading kubeconfig: {e}")
        logging.error(f"Error loading kubeconfig: {e}")
        raise

    core_api = client.CoreV1Api()
    # 用來儲存符合條件的節點名稱
    computing_nodes = set()
    
    # 獲取所有節點的標籤
    nodes = core_api.list_node().items
    for node in nodes:
        labels = node.metadata.labels
        # 檢查標籤是否符合條件
        if labels.get('arha-node-type') == 'computing-node':
            computing_nodes.add(node.metadata.name)
    
    return computing_nodes

async def check_pod_container_status(namespace, node_name):
    """
    檢查節點上的所有容器狀態，直到所有容器都恢復正常運行。
    如果有容器的狀態是 CrashLoopBackOff，則重啟該 Pod。
    """
    threshhold = 7
    ready_counter = 0
    query = (
    '(kube_pod_container_status_ready{{namespace="{namespace}"}} unless '
    '(label_replace(kube_pod_container_status_terminated_reason{{reason="Completed", namespace="{namespace}"}}, '
    '"reason", "", "reason", "(.*)"))) * on (pod, namespace) group_left(node) '
    'kube_pod_info{{node="{node_name}"}}'
).format(namespace=namespace, node_name=node_name)

    try:
        config.load_incluster_config() if IN_CLUSTER else config.load_kube_config()
    except Exception as e:
        logging.error(f"Error loading kubeconfig: {e}")
        raise

    core_api = client.CoreV1Api()

    while True:
        ready = True
        try:
            response = requests.get(PROMETHEUS_URL, params={'query': query}, timeout=3)
            if response.status_code == 200:
                data = response.json()
                results = data['data']['result']

                if results:
                    for result in results:
                        pod_name = result['metric']['pod']
                        status = result['value'][1]
                        if status == '0':  # 容器未準備
                            logging.info(f"Container in Pod {pod_name} is not ready.")
                            ready = False
                            ready_counter = 0
                            
                            # 獲取 Pod 詳細資訊，檢查容器狀態
                            pod = core_api.read_namespaced_pod(name=pod_name, namespace=namespace)
                            for container_status in pod.status.container_statuses:
                                if container_status.state.waiting and container_status.state.waiting.reason == "CrashLoopBackOff":
                                    logging.warning(f"Container {container_status.name} in Pod {pod_name} is in CrashLoopBackOff. Restarting Pod.")
                                    core_api.delete_namespaced_pod(name=pod_name, namespace=namespace)
                                    logging.info(f"Pod {pod_name} deleted. Kubernetes will recreate it.")
                                    break
                            
                    if ready:
                        ready_counter += 1
                        if ready_counter >= threshhold:
                            logging.info("所有容器已準備好。")
                            return True
                else:
                    logging.error("No data returned from Prometheus.")
                    
            else:
                logging.error(f"Failed to fetch data from Prometheus. Status code: {response.status_code}")
                return False
        except requests.exceptions.Timeout:
            logging.error("Query timeout while fetching data from Prometheus.")
            return False
        except Exception as e:
            logging.error(f"Error while checking container statuses: {e}")
            
        await asyncio.sleep(5)

async def check_and_post_node_recovery(namespace, node_name):
    """
    檢查容器狀態，並在所有容器恢復正常後向 Controller 發送 POST 請求。
    """
    logging.info(f"Starting to monitor containers on node {node_name} in namespace {namespace}...")
    success = await check_pod_container_status(namespace, node_name)

    if success:
        logging.info(f"All gpu-operator containers on node {node_name} are healthy. Sending recovery signal to Controller.")
        request_to_controller = {
            "nodeName": node_name
        }
        url = "http://controller-service:80/noderecovery"
        try:
            response = requests.post(url, data=json.dumps(request_to_controller))
            if response.status_code == 200:
                logging.info(f"Successfully sent recovery signal for node {node_name} to Controller.")
            else:
                logging.error(f"Failed to send recovery signal: {response.status_code}, {response.text}")
        except Exception as e:
            logging.error(f"Error while sending POST request: {e}")

async def handle(body):
    async with httpx.AsyncClient(timeout=60) as client:
        await client.post(controller_alert_url, json=body)

async def restart_pods_on_node(namespace, node_name):

    try:
        config.load_incluster_config() if IN_CLUSTER else config.load_kube_config()
    except Exception as e:
        logging.error(f"Error loading kubeconfig: {e}")
        raise

    # 初始化 Kubernetes API 客戶端
    v1 = client.CoreV1Api()

    try:
        # 獲取指定 namespace 中的所有 Pod
        pods = v1.list_namespaced_pod(namespace)

        for pod in pods.items:
            # 檢查 Pod 是否在特定的節點上
            if pod.spec.node_name == node_name:
                pod_name = pod.metadata.name
                print(f"重啟 Pod: {pod_name} 在節點 {node_name} 上")
                logging.info(f"重啟 Pod: {pod_name} 在節點 {node_name} 上")

                # 刪除 Pod 來觸發重新啟動
                v1.delete_namespaced_pod(name=pod_name, namespace=namespace)
                print(f"Pod {pod_name} 已經被刪除，Kubernetes 將自動重新調度並啟動")
                logging.info(f"Pod {pod_name} 已經被刪除，Kubernetes 將自動重新調度並啟動")

    except Exception as e:
        print(f"API 請求失敗: {e}")

async def check_node_status():
    computing_nodes_set = await get_computing_nodes()
    computing_nodes_status = {node: 'Ready' for node in computing_nodes_set}

    while True:
        query = 'kube_node_status_condition{condition="Ready",status="true"}'
        try:
            response = requests.get(PROMETHEUS_URL, params={'query': query}, timeout=3)
            if response.status_code == 200:
                data = response.json()
                results = data['data']['result']

                if results:
                    for result in results:
                        node_name = result['metric']['node']
                        status = result['value'][1]
                        if status != '1' and node_name in computing_nodes_set and computing_nodes_status[node_name] != 'NotReady':
                            computing_nodes_status[node_name] = 'NotReady'
                            logging.info(f"Node {node_name} becomes Not Ready")
                            request_to_controller = {
                                "alertType" : "workernode_failure",
                                "alertContent" : {
                                    "nodeName" : node_name
                                }
                            }
                            asyncio.create_task(handle(request_to_controller))
                            logging.info("Send node failure alert to Controller")
                        elif status == '1' and node_name in computing_nodes_set and computing_nodes_status[node_name] != 'Ready':
                            logging.info(f"Node {node_name} becomes Ready")
                            computing_nodes_status[node_name] = 'Ready'
                            # await restart_pods_on_node('gpu-operator', node_name)
                            asyncio.create_task(check_and_post_node_recovery('gpu-operator', node_name))          
                else:
                    print("No nodes found with the specified condition.")
            else:
                print("Failed to fetch data from Prometheus.")
        except requests.exceptions.Timeout:
            print("Query kube_node_status_condition timeout")

        print(computing_nodes_status)
        await asyncio.sleep(5)

async def main():
    await asyncio.gather(
        check_node_status()
    )

if __name__ == '__main__':
    asyncio.run(main())