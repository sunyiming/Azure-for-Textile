import datetime
import logging
import os
import re
import json
import uuid
import requests
from bs4 import BeautifulSoup

import azure.functions as func
from azure.storage.blob import BlobServiceClient, BlobClient, ContainerClient
from azure.core.exceptions import ResourceExistsError
from azure.cosmos import CosmosClient

def main(mytimer: func.TimerRequest) -> None:
    utc_timestamp = datetime.datetime.utcnow().replace(
        tzinfo=datetime.timezone.utc).isoformat()

    if mytimer.past_due:
        logging.info('The timer is past due!')

    logging.info('Python timer trigger function ran at %s', utc_timestamp)

    
    # Get conn string from env and create client
    # 获取环境变量中的存储服务连接字符串
    connect_str = os.getenv('AZURE_STORAGE_CONNECTION_STRING')
    # 连接至存储服务
    blob_service_client = BlobServiceClient.from_connection_string(connect_str)
    # 获取环境变量中的Cosmos DB的主机名、Cosmos DB的主密钥
    cosmos_host = os.getenv('COSMOS_HOST')
    cosmos_master_key = os.getenv('COSMOS_MASTER_KEY')
    # 连接至 Cosmos DB
    cosmos_client = CosmosClient(cosmos_host, {'masterKey': cosmos_master_key} )


    header={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/70.0.3538.77 Safari/537.36','Accept': '*/*','Connection': 'keep-alive','Host': 'news.ctei.cn','Referer': 'http://news.ctei.cn/domestic/gnzx/','Cache-Control': 'max-age=0'}
    # Contain news obj
    news_list = []

    try:
        # Get news links and store into news_obj
        r = requests.get("http://news.ctei.cn/domestic/gnzx/", headers = header)
        soup = BeautifulSoup(str(r.content,"utf-8"), 'html.parser')
        html_lis = soup.select(".news_text ul li")
        for html_li in html_lis:
            news_obj = {
                "id": None,
                "title": html_li.find('a').get("title"), 
                "url": "http://news.ctei.cn/domestic/gnzx/" + html_li.find('a').get("href")[2:], 
                "image_filenames": [], 
                "date": None, 
                "content": None  
            }
            news_obj['id'] = str(uuid.uuid3(uuid.NAMESPACE_URL, news_obj['url']))
            news_list.append(news_obj)
        
        # Get more news
        for i in range(1, 16):
            r = requests.get("http://news.ctei.cn/domestic/gnzx/index_" + str(i) + ".html", headers = header)
            soup = BeautifulSoup(str(r.content,"utf-8"), 'html.parser')
            html_lis = soup.select(".news_text ul li")
            for html_li in html_lis:
                news_obj = {
                    "id": None,
                    "title": html_li.find('a').get("title"), 
                    "url": "http://news.ctei.cn/domestic/gnzx/" + html_li.find('a').get("href")[2:], 
                    "image_filenames": [], 
                    "date": None, 
                    "content": None  
                }
                news_obj['id'] = str(uuid.uuid3(uuid.NAMESPACE_URL, news_obj['url']))
                news_list.append(news_obj)
        
        # 1. Get images from news and store into Azure Blob
        # 2. Add filename to news_obj
        for news_obj in range(len(news_list)):
            news_obj = news_list[news_obj]
            logging.info(news_obj['url'])

            # Get news main content
            r = requests.get(news_obj['url'], headers = header)
            soup = BeautifulSoup(str(r.content,"utf-8"), 'html.parser')
            main_content_html = soup.find("div", {"class": "TRS_Editor"})
            date_to_find = soup.find("table", {"class": "infobian"})
            # news_obj['date'] = datetime.datetime.strptime(re.search("\d{4}-\d{2}-\d{2}", str(date_to_find)).group(), "%Y-%m-%d")
            try:
                news_obj['date'] = re.search("\d{4}-\d{2}-\d{2}", str(date_to_find)).group()
            except Exception:
                logging.exception("EXCEPTION:")
                continue

            # Find all images and save into blob
            for img_html in main_content_html.findAll("img"):
                img_filename = img_html.get("src")[2:]
                img_url = news_obj['url'][:news_obj['url'].rfind('/')] + '/' + img_filename
                logging.info("Img url:" + img_url)
                # Get news image and save into blob
                img = requests.get(img_url)
                try:
                     # 指定 container 为 save-images
                    blob_client = blob_service_client.get_blob_client(container="save-images", blob=img_filename)
                    # 上传文件
                    blob_client.upload_blob(img.content)
                # File already exists
                except ResourceExistsError:
                    logging.warning("BlobAlreadyExists: " + img_filename)
                else:
                    logging.info("Uploaded blob: " + img_filename)
                finally:
                    news_obj['image_filenames'].append(img_filename)
            # if len(main_content_html.findAll("img")) != 0:
            #     break

            # Get main content
            [e.decompose() for e in main_content_html.find_all("style")]
            [e.decompose() for e in main_content_html.find_all("script")]
            news_obj['content'] = main_content_html.text
        
        # logging.info(json.dumps(news_list))

        # Upsert news_obj to Azure Cosmos 上传新闻内容
        # 列表 news_list 中存放新闻对象 news_obj
        # 连接至数据库 TextileDB，container 为 News
        container = cosmos_client.get_database_client("TextileDB").get_container_client("News")
        # 向数据库插入或更新对象
        for news_obj in news_list:
            container.upsert_item(news_obj)

        
    except Exception:
        logging.exception("EXCEPTION:")