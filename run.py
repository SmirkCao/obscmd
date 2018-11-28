#!/usr/bin/python
# -*- coding:utf-8 -*-
import Queue
import base64
import hashlib
import logging
import logging.config
import logging.handlers
import multiprocessing
import os
import sys
import threading
import time
import traceback
from optparse import OptionParser

import util
import obspycmd
import results
import myLib.cloghandler
from copy import deepcopy
from Queue import Empty
from constant import ConfigFile
from constant import LOCAL_SYS
from constant import SYS_ENCODING
from constant import CONTENT_TYPES
from util import Counter
from util import ThreadsStopFlag
from util import RangeFileWriter
from util import User

logging.handlers.ConcurrentRotatingFileHandler = myLib.cloghandler.ConcurrentRotatingFileHandler

VERSION = 'v4.6.7'
RETRY_TIMES = 3
UPLOAD_PART_MIN_SIZE = 5 * 1024 ** 2
UPLOAD_PART_MAX_SIZE = 5 * 1024 ** 3
TEST_CASES = {
    201: 'PutObject;put_object',
    202: 'GetObject;get_object',
    206: 'CopyObject;copy_object'
}

OBJECTS_QUEUE_SIZE = 10 ** 5
END_MARKER = "END_MARKER"

user = None

# configurations
running_config = {}

# upload tasks
all_files_queue = multiprocessing.Queue()

# download tasks
all_objects_queue = multiprocessing.Queue()

# statistic tasks
results_queue = multiprocessing.Queue()

# lock for process workers
lock = multiprocessing.Lock()

# lock for process workers result
lock_re = multiprocessing.Lock()

# result file for object manifest
manifest_file = ''

# count for all workers' concurrency
current_concurrency = multiprocessing.Value('i', 0)

# data size of all tasks
total_data = multiprocessing.Value('f', 0)
total_data_upload = 0
total_data_download = 0
number_of_objects_to_put = 0


def read_config(options, config_file_name=ConfigFile.FILE_CONFIG):
    global user
    try:
        print 'start read file \n'
        f = open(config_file_name, 'rw')
        lines = f.readlines()
        for line in lines:
            line = line.strip()
            if line and line[0] != '#':
                running_config[line[:line.find('=')].strip()] = line[line.find(
                    '=') + 1:].strip()
            else:
                continue
        f.close()

        if (options.localPath or options.remoteDir) and (
                    options.downloadTarget or options.savePath):
            parser.error("options are mutually exclusive")
        if options.operation:
            running_config['Operation'] = options.operation
        if options.localPath:
            running_config['LocalPath'] = options.localPath
        if options.remoteDir:
            running_config['RemoteDir'] = options.remoteDir
        if options.downloadTarget:
            running_config['DownloadTarget'] = options.downloadTarget
        if options.savePath:
            running_config['SavePath'] = options.savePath
        if options.bucketName:
            running_config['BucketNameFixed'] = options.bucketName
        if options.AK:
            running_config['AK'] = options.AK
        if options.SK:
            running_config['SK'] = options.SK
        if options.DomainName:
            running_config['DomainName'] = options.DomainName
        if options.Region:
            running_config['Region'] = options.Region

        running_config['AK'] = prompt_for_input('AK', 'your account')
        running_config['SK'] = prompt_for_input('SK', 'your account')
        user = User('obscmd', running_config['AK'], running_config['SK'])
        # Don't show SK on screen display
        del running_config['SK']

        if running_config['IsHTTPs'].lower() == 'true':
            running_config['IsHTTPs'] = True
        else:
            running_config['IsHTTPs'] = False

        running_config['ConnectTimeout'] = int(running_config['ConnectTimeout'])
        if int(running_config['ConnectTimeout']) < 5:
            running_config['ConnectTimeout'] = 5

        if running_config['RemoteDir']:
            running_config['RemoteDir'] = running_config['RemoteDir'].replace(
                '\\', '/').strip('/')
            if running_config['RemoteDir']:
                running_config['RemoteDir'] = running_config['RemoteDir'] + '/'

        running_config['DownloadTarget'] = running_config[
            'DownloadTarget'].lstrip('/')

        if running_config['VirtualHost'].lower() == 'true':
            running_config['VirtualHost'] = True
        else:
            running_config['VirtualHost'] = False

        if running_config['RecordDetails'].lower() == 'true':
            running_config['RecordDetails'] = True
        else:
            running_config['RecordDetails'] = False

        if running_config['BadRequestCounted'].lower() == 'true':
            running_config['BadRequestCounted'] = True
        else:
            running_config['BadRequestCounted'] = False

        if running_config['PrintProgress'].lower() == 'true':
            running_config['PrintProgress'] = True
        else:
            running_config['PrintProgress'] = False

        if running_config['IgnoreExist'].lower() == 'true':
            running_config['IgnoreExist'] = True
        else:
            running_config['IgnoreExist'] = False

        if running_config['CompareETag'].lower() == 'true':
            running_config['CompareETag'] = True
        else:
            running_config['CompareETag'] = False

        if running_config['CheckFileChanging'].lower() == 'true':
            running_config['CheckFileChanging'] = True
        else:
            running_config['CheckFileChanging'] = False

        if running_config['ArchiveAfterUpload'].lower() == 'true':
            running_config['ArchiveAfterUpload'] = True
        else:
            running_config['ArchiveAfterUpload'] = False

        if running_config['CheckRoot'].lower() == 'true':
            running_config['CheckRoot'] = True
        else:
            running_config['CheckRoot'] = False

        if running_config['CheckSoftLinks'].lower() == 'true':
            running_config['CheckSoftLinks'] = True
        else:
            running_config['CheckSoftLinks'] = False

        if running_config['ProxyPort']:
            running_config['ProxyPort'] = int(running_config['ProxyPort'])

        # User's input
        running_config['Operation'] = prompt_for_input('Operation',
                                                       'operation(upload/download/copy)')
        if not running_config['Operation'].lower() == 'upload' and not \
                        running_config['Operation'].lower() == 'download'and not \
                        running_config['Operation'].lower() == 'copy':
            print 'Operation must be upload or download or copy, exit...'
            exit()

        if running_config['Operation'].lower() == 'upload':
            running_config['Testcase'] = 201
        elif running_config['Operation'].lower() == 'download':
            running_config['Testcase'] = 202
        elif running_config['Operation'].lower() == 'copy':
            running_config['Testcase'] = 206
        if running_config.get('MultipartObjectSize'):
            running_config['PartSize'] = prompt_for_input('PartSize',
                                                          'multipart size')
            if running_config['Operation'].lower() == 'upload' and int(
                    running_config['PartSize']) < UPLOAD_PART_MIN_SIZE:
                running_config['PartSize'] = str(UPLOAD_PART_MIN_SIZE)
            if running_config['Operation'].lower() == 'upload' and int(
                    running_config['PartSize']) > UPLOAD_PART_MAX_SIZE:
                running_config['PartSize'] = str(UPLOAD_PART_MAX_SIZE)
            if int(running_config['PartSize']) > int(
                    running_config.get('MultipartObjectSize')):
                print 'In order to cut object(s) to pieces, PartSize must be less than MultipartObjectSize'
                exit()
        else:
            running_config['MultipartObjectSize'] = '0'

        running_config['Concurrency'] = int(running_config['Concurrency']) if \
            running_config['Concurrency'] else 1
        running_config['LongConnection'] = False
        running_config['ConnectionHeader'] = ''
        running_config['CollectBasicData'] = False
        running_config['LatencyRequestsNumber'] = False
        running_config['LatencyPercentileMap'] = False
        running_config['StatisticsInterval'] = 3
        running_config['LatencySections'] = '500,1000,3000,10000'

        # If server side encryption is on, set https + AWSV4 on.
        if running_config['SrvSideEncryptType']:
            if not running_config['IsHTTPs']:
                running_config['IsHTTPs'] = True
                logging.warn(
                    'change IsHTTPs to True while use SrvSideEncryptType')
            if running_config['AuthAlgorithm'] != 'AWSV4' and running_config[
                'SrvSideEncryptType'].lower() == 'sse-kms':
                running_config['AuthAlgorithm'] = 'AWSV4'
                logging.warn(
                    'change AuthAlgorithm to AWSV4 while use SrvSideEncryptType = SSE-KMS')
    except IOError,data:
        print '[ERROR] Read config file %s error: %s' % (config_file_name, data)
        sys.exit()


def initialize_object_name(target_in_local, keys_already_exist_list):
    global total_data_upload
    global number_of_objects_to_put

    remote_dir = running_config['RemoteDir']
    multi_part_object_size = int(running_config.get('MultipartObjectSize'))
    part_size = int(running_config['PartSize'])

    def generate_task_tuple(file_path):
        global total_data_upload
        file_path = file_path.strip()
        if not os.path.isfile(file_path):
            print '{target} is not a file. Skip it.'.format(target=file_path)
        else:
            key = os.path.split(file_path)[1]

            if remote_dir:
                key = remote_dir + key

            task_tuple = None
            if key.decode(SYS_ENCODING) not in keys_already_exist_list:
                size = int(os.path.getsize(file_path))
                total_data_upload += size
                if size >= multi_part_object_size:
                    parts = size / part_size + 1
                    if parts > 10000:
                        msg_t = 'PartSize({part_size}) is too small.\n' \
                                'You have a file({file}) cut to more than 10,000 parts.\n' \
                                'Please make sure every file is cut to less than or equal to 10,000 parts. Exit...' \
                            .format(part_size=running_config['PartSize'],
                                    file=key)
                        print msg_t
                        logging.warn(msg_t)
                        exit()

                task_tuple = (key, size, file_path)
            return task_tuple

    if ',' not in target_in_local:
        if running_config['CheckSoftLinks'] and os.path.islink(target_in_local):
            logging.error(
                "the local path [%s] is link, now exit!" % target_in_local)
            exit()
        if os.path.isdir(target_in_local):
            top_dir = running_config['LocalPath'].split('/')[-1]
            files = []
            try:
                files = os.listdir(target_in_local)
            except OSError:
                pass
            if not files:
                object_to_put = target_in_local.replace(
                    running_config['LocalPath'], top_dir)
                object_to_put = object_to_put.lstrip('/') + '/'
                key = target_in_local + '/'
                if remote_dir:
                    object_to_put = remote_dir + object_to_put
                all_files_queue.put((object_to_put, 0, key))
                logging.debug("=== object_to_put : %s, keyfile : %s ===" % (
                object_to_put, key))
                number_of_objects_to_put += 1
            else:
                for fi in files:
                    fi_d = os.path.join(target_in_local, fi)
                    if running_config['CheckSoftLinks'] and os.path.islink(fi_d):
                        logging.warning(
                            'skip the file[%s] because it is link!' % fi_d)
                        continue
                    if os.path.isdir(fi_d):
                        logging.debug('scanning dir: ' + fi_d)
                        initialize_object_name(fi_d, keys_already_exist_list)
                    elif os.path.isfile(fi_d):
                        object_to_put = fi_d.replace(
                            running_config['LocalPath'], top_dir)
                        object_to_put = object_to_put.lstrip('/')

                        if remote_dir:
                            object_to_put = remote_dir + object_to_put

                        if object_to_put.decode(
                                SYS_ENCODING) not in keys_already_exist_list:
                            object_size = int(os.path.getsize(fi_d))
                            total_data_upload += object_size
                            if object_size >= multi_part_object_size:
                                parts_count = object_size / part_size + 1
                                if parts_count > 10000:
                                    msg = 'PartSize({part_size}) is too small.\n' \
                                          'You have a file({file}) cut to more than 10,000 parts.\n' \
                                          'Please make sure every file is cut to less than or equal to 10,000 parts.\n' \
                                          'Exit...' \
                                        .format(
                                        part_size=running_config['PartSize'],
                                        file=object_to_put)
                                    print msg
                                    logging.error(msg)
                                    exit()

                            all_files_queue.put(
                                (object_to_put, object_size, fi_d))
                            number_of_objects_to_put += 1
        elif os.path.isfile(target_in_local):
            task_t = generate_task_tuple(target_in_local)
            if task_t:
                all_files_queue.put(task_t)
                number_of_objects_to_put += 1
    else:
        targets = target_in_local.split(',')
        targets = list(set(targets))
        for target in targets:
            task_t = generate_task_tuple(target)
            if task_t:
                all_files_queue.put(task_t)
                number_of_objects_to_put += 1

''' old get bucket,limit in key numbers in bucket
def get_all_keys_in_bucket(bucket_name, ak, sk, target_in_bucket=''):
    from xml.etree import ElementTree
    m = 'getting keys in bucket...'
    print m
    logging.warn(m)
    lists_objects = []
    targets = list(set(target_in_bucket.split(',')))
    for target in targets:
        target = target.strip()
        list_objects = []
        marker = ''

        while marker is not None:
            conn = obspycmd.MyHTTPConnection(host=running_config['DomainName'],
                                             is_secure=running_config[
                                                 'IsHTTPs'],
                                             ssl_version=running_config[
                                                 'sslVersion'],
                                             timeout=running_config[
                                                 'ConnectTimeout'],
                                             long_connection=running_config[
                                                 'LongConnection'],
                                             conn_header=running_config[
                                                 'ConnectionHeader'],
                                             proxy_host=running_config[
                                                 'ProxyHost'],
                                             proxy_port=running_config[
                                                 'ProxyPort'],
                                             proxy_username=running_config[
                                                 'ProxyUserName'],
                                             proxy_password=running_config[
                                                 'ProxyPassWord'])
            rest = obspycmd.OBSRequestDescriptor(
                request_type='ListObjectsInBucket',
                ak=ak, sk=sk,
                auth_algorithm=running_config['AuthAlgorithm'],
                virtual_host=running_config['VirtualHost'],
                domain_name=running_config['DomainName'],
                region=running_config['Region'])
            rest.bucket = bucket_name

            # List a directory
            if target.endswith('/'):
                dir_prefix = target.strip('/')
                if dir_prefix:
                    dir_prefix = dir_prefix + '/'
                    rest.query_args['prefix'] = dir_prefix
            elif target.endswith('*'):
                prefix = target.strip('/').rstrip('*')
                if prefix:
                    rest.query_args['prefix'] = prefix
            # List an object
            elif target:
                rest.query_args['prefix'] = target

            if marker:
                rest.query_args['marker'] = marker

            resp = obspycmd.OBSRequestHandler(rest, conn).make_request()
            marker = resp.return_data
            xml_body = resp.recv_body
            logging.debug("=== response body is %s ===" % xml_body)

            if not xml_body:
                print 'Error in http request, please see log/*.log'
                exit()

            if '<Code>NoSuchBucket</Code>' in xml_body:
                print 'No such bucket(%s), exit...' % bucket_name
                logging.error('No such bucket(%s), exit...' % bucket_name)
                exit()

            root = ElementTree.fromstring(xml_body)
            logging.debug("=== the elementTree of xml_body is %s ===" % root)
            if '<Contents>' in xml_body:
                logging.debug("=== root[6:] is %s, and root[5:] is %s ===" % (
                root[6:], root[5:]))
                if '<NextMarker>' in xml_body:
                    for contents_element in root[6:]:
                        if contents_element[0].text[-1] != '/':
                            # list_objects.append((contents_element[0].text,
                            #                      int(contents_element[3].text)))
                            all_objects_queue.put((contents_element[0].text,
                                                 int(contents_element[3].text)))

                else:
                    for contents_element in root[5:]:
                        logging.debug(
                            "=== contents_element is %s, contents_element[0] is %s, contents_element[3] is %s ===" % (
                            contents_element, contents_element[0].text,
                            contents_element[3].text))
                        if contents_element[0].text[-1] != '/' or int(
                                contents_element[3].text) == 0:
                            list_objects.append((contents_element[0].text,
                                                 int(contents_element[3].text)))

        # If target is a single object, check if it's in the bucket.
        if target and not target.endswith(('/', '*')):
            find_flag = False
            for one_tuple in list_objects:
                if target == one_tuple[0].encode(SYS_ENCODING):
                    find_flag = True
                    break
            if not find_flag:
                list_objects = []
        lists_objects.extend(list_objects)
    logging.debug("=== lists_objects is %s ===" % lists_objects)
    return list(set(lists_objects))
'''
def get_all_keys_in_bucket(bucket_name, ak, sk, target_in_bucket='', capacity_limitation=False, versions=False):
    try:
        from xml.etree import ElementTree
        m = 'getting keys in bucket...'
        logging.warn(m)
        targets = list(set(target_in_bucket.split('\\')))
        for target in targets:
            target = target.strip()
            marker = ''
            if versions:
                # 多版本暂不支持指定marker
                marker = ('', '')

            while marker is not None:
                conn = obspycmd.MyHTTPConnection(host=running_config['DomainName'],
                                                 is_secure=running_config['IsHTTPs'],
                                                 ssl_version=running_config['sslVersion'],
                                                 timeout=running_config['ConnectTimeout'],
                                                 long_connection=running_config['LongConnection'],
                                                 conn_header=running_config['ConnectionHeader'])
                rest = obspycmd.OBSRequestDescriptor(request_type='ListObjectsInBucket',
                                                     ak=ak, sk=sk,
                                                     auth_algorithm=running_config['AuthAlgorithm'],
                                                     virtual_host=running_config['VirtualHost'],
                                                     domain_name=running_config['DomainName'],
                                                     region=running_config['Region'])
                rest.bucket = bucket_name
                # rest.headers['x-hws-offline-migrate'] = True   # sepcical use,don't care
                rest.query_args['prefix'] = target
                if versions:
                    rest.query_args['versions'] = None
                    if marker:
                        # rest.query_args['key-marker'] = marker[0]
                        rest.query_args['version-id-marker'] = marker[1]
                else:
                    if marker:
                        rest.query_args['marker'] = marker

                resp = obspycmd.OBSRequestHandler(rest, conn).make_request()

                if resp.status != '200 OK':
                    # scan finished, put end marker without checking capacity limitation.
                    # all_objects_queue.put(END_MARKER)
                    print 'Failed to list objects[%s]' % resp.status
                    logging.error('Failed to list bucket(%s), err=[%s], exit...' % (bucket_name, resp.status))
                    exit()

                marker = resp.return_data
                xml_body = resp.recv_body
                if not xml_body:
                    # scan finished, put end marker without checking capacity limitation.
                    # all_objects_queue.put(END_MARKER)
                    print 'Error in http request, please see log/*.log'
                    exit()
                if '<Code>NoSuchBucket</Code>' in xml_body:
                    print 'No such bucket(%s), exit...' % bucket_name
                    logging.error('No such bucket(%s), exit...' % bucket_name)
                    exit()
                logging.info('>>>>> response body: %s >>>>>' % xml_body)

                root = ElementTree.fromstring(xml_body)
                if versions:
                    # if '<Version>' in xml_body:
                    elements = root[8:] if marker else root[6:]
                else:
                    # if '<Contents>' in xml_body:
                    elements = root[6:] if marker else root[5:]

                for contents_element in elements:
                    if contents_element[0].text[-1] != '/':
                        if capacity_limitation:
                            # 如果队列已满，等待1s后再重试
                            while all_objects_queue.qsize() >= OBJECTS_QUEUE_SIZE:
                                logging.info(
                                    "all_objects_queue is full[%d], wait 1s..." % all_objects_queue.qsize())
                                time.sleep(1)

                        if versions:
                            # （对象名，versionId，eTag，ContentLength）
                            all_objects_queue.put((contents_element[0].text, contents_element[1].text,
                                                   contents_element[4].text.strip('"'), int(contents_element[5].text)))
                        else:
                            # （对象名，None版本，eTag，ContentLength）
                            all_objects_queue.put((contents_element[0].text, None,
                                                   contents_element[2].text.strip('"'), int(contents_element[3].text)))
    except KeyboardInterrupt, data:
        logging.warn("list object exit...[%s]" % data)
        return
    finally:
        # scan finished, put end marker without checking capacity limitation.
        all_objects_queue.put(END_MARKER)

def JudgeObjectIsExist(object_tuple,bucketname,conn,worker_id):
    rest = obspycmd.OBSRequestDescriptor(request_type='HeadObject',
                                         ak=user.ak, sk=user.sk,
                                         auth_algorithm=running_config['AuthAlgorithm'],
                                         virtual_host=running_config['VirtualHost'],
                                         domain_name=running_config['DomainName'],
                                         region=running_config['Region'])
    rest.bucket = bucketname
    rest.key = object_tuple[0].encode('utf8')
    # 如果是带版本列举，则需要带版本拷贝
    if object_tuple[1] is not None:
        rest.query_args['versionId'] = object_tuple[1].encode('utf8')
    # rest.headers['x-hws-offline-migrate'] = True

    resp = obspycmd.OBSRequestHandler(rest, conn).make_request()
    logging.info("HeadObject resp.status[%s]", resp.status)
    if resp.status == "200 OK":
        logging.warn("Object[%s/%s] already exist in dst bucket[%s], src.e_tag[%s], src.content_length=[%s], "
                     "dst.e_tag=[%s], dst.content_length=[%s], " % (
                         str(rest.key), str(object_tuple[1]), rest.bucket,
                         str(object_tuple[2]), str(object_tuple[3]), resp.e_tag,
                         resp.content_length))

        if resp.e_tag == object_tuple[2] and resp.content_length == object_tuple[3]:
            results_queue.put(
                (worker_id, object_tuple[0], object_tuple[2], rest.request_type, resp.start_time,
                 resp.end_time, resp.send_bytes, resp.recv_bytes, rest.record_url,
                 resp.e_tag, 'AlreadyExist', resp.request_id)
            )
        else:
            results_queue.put(
                (worker_id, object_tuple[0].encode('utf8'), object_tuple[2], rest.request_type, resp.start_time,
                 resp.end_time, resp.send_bytes, resp.recv_bytes, rest.record_url,
                 resp.e_tag, 'Conflict', resp.request_id)
            )
        return True
    if resp.status != "404 Not Found":
        logging.warn(
            "Failed to Head Object[%s/%s] in bucket[%s], can't do copy." % (str(rest.key), str(object_tuple[1]),
                                                                            rest.bucket))
        results_queue.put(
            (worker_id, object_tuple[0], object_tuple[2], rest.request_type, resp.start_time,
             resp.end_time, resp.send_bytes, resp.recv_bytes, rest.record_url,
             resp.e_tag, 'UnknownError', resp.request_id)
        )
        return True
    else:
        return False

def copy_object(worker_id, conn):
    from xml.etree import ElementTree
    try:
        while True:
            while all_objects_queue.empty():
                logging.info("all_objects_queue is empty, wait 1s...[%s]." % worker_id)
                time.sleep(1)

            try:
                object_tuple = all_objects_queue.get()
                logging.info('get_object tuple:' + str(object_tuple))
            except Empty:
                continue

            # 对象上传完成
            if object_tuple == END_MARKER:
                logging.warn("object copy finished[%s]." % worker_id)
                all_objects_queue.put(object_tuple)
                break

            # Head Object
            # if Object exist
            #    continue

            objectIsExist=JudgeObjectIsExist(object_tuple, running_config['CopyDstBucket'], conn, worker_id)

            if objectIsExist:
                continue

            import urllib
            copy_source = '/' + running_config['CopySrcBucket'] + '/' + urllib.quote(object_tuple[0].encode('utf-8'))
            if object_tuple[1] is not None:
                copy_source = copy_source + '?versionId=' + object_tuple[1].encode('utf8')

            # make Copy Object request
            rest = obspycmd.OBSRequestDescriptor(request_type='CopyObject',
                                                 ak=user.ak, sk=user.sk,
                                                 auth_algorithm=running_config['AuthAlgorithm'],
                                                 virtual_host=running_config['VirtualHost'],
                                                 domain_name=running_config['DomainName'],
                                                 region=running_config['Region'])
            rest.bucket = running_config['CopyDstBucket']
            rest.key = object_tuple[0].encode('utf8')
            file_name = object_tuple[0].encode(SYS_ENCODING)
            rest.headers['x-amz-copy-source'] = copy_source
            logging.debug("=== object_tuple[0]:%s ; file_name:%s ; size:%s ===" % (
                str(object_tuple[0].encode('utf8')), str(file_name), str(object_tuple[3])))

            resp = obspycmd.OBSRequestHandler(rest, conn).make_request()

            e_tag = ''
            status = resp.status
            if resp.status == "200 OK":
                # 校验返回消息中的md5,resp中未解析e_tag,拷贝请求的e_tag在body体内
                xml_body = resp.recv_body
                root = ElementTree.fromstring(xml_body)
                e_tag = root[1].text.strip('"')
                logging.debug(
                    "xml_body=[%s], e_tag=[%s], object_tuple[2]=[%s]" % (str(xml_body), e_tag, str(object_tuple[2])))
                if e_tag != object_tuple[2]:
                    logging.warn(
                        "WARN:etag mismatching, the data[%s/%s] may be inconsistent." % (rest.bucket, rest.key))
                    status = "Inconsistent"

            results_queue.put(
                (worker_id, copy_source, object_tuple[2], rest.request_type, resp.start_time,
                 resp.end_time, resp.send_bytes, resp.recv_bytes, rest.record_url,
                 e_tag, status, resp.request_id)
            )

    except KeyboardInterrupt, data:
        logging.warn("copy object exit...[%s]" % data)
        return



def put_object(worker_id, conn):
    while not all_files_queue.empty():
        try:
            file_tuple = all_files_queue.get(block=False)
            logging.debug('Id of the worker is %s, and put_object tuple: %s' % (
                str(worker_id), str(file_tuple)))
            # Check if this file is changing. If true, skip it.
            if running_config['CheckFileChanging'] and not str(
                    file_tuple[2]).endswith('/'):
                m_time = os.stat(file_tuple[2]).st_mtime
                # For improve upload performance , change sleep time from 2s to 100ms
                time.sleep(0.1)
                if os.stat(file_tuple[2]).st_mtime != m_time:
                    result_for_manifest = '%s,   , %s,   ,   , FAILED:file is changing \n' % (
                        file_tuple[2], file_tuple[1])
                    logging.debug("=== the result is %s" % result_for_manifest)
                    update_objects_manifest(result_for_manifest)
                    logging.warn(
                        'File(%s) is changing, skip it!' % file_tuple[2])
                    continue
        except Empty:
            logging.debug(
                "Empty when getting task tuple from queue,then continue.")
            continue

        if file_tuple[1] < int(running_config.get('MultipartObjectSize')):
            rest = obspycmd.OBSRequestDescriptor(request_type='PutObject',
                                                 ak=user.ak, sk=user.sk,
                                                 auth_algorithm=running_config[
                                                     'AuthAlgorithm'],
                                                 virtual_host=running_config[
                                                     'VirtualHost'],
                                                 domain_name=running_config[
                                                     'DomainName'],
                                                 region=running_config[
                                                     'Region'])
            try:
                rest.key = file_tuple[0]
            except UnicodeDecodeError:
                logging.error('Decode error, key: ' + file_tuple[0])
                result_for_manifest = '%s,   , %s,   ,   , FAILED:decode error before upload\n' % (
                    file_tuple[2], file_tuple[1])
                logging.debug("=== the result is %s" % result_for_manifest)
                update_objects_manifest(result_for_manifest)
                continue
            rest.bucket = running_config['BucketNameFixed']
            rest.headers['content-type'] = 'application/octet-stream'
            tokens = file_tuple[0].split('.')
            if len(tokens) > 1:
                suffix = tokens[-1].strip().lower()
                if suffix in CONTENT_TYPES:
                    rest.headers['content-type'] = CONTENT_TYPES[suffix]

            if running_config['PutWithACL']:
                rest.headers['x-amz-acl'] = running_config['PutWithACL']
            if running_config['SrvSideEncryptType'].lower() == 'sse-c':
                rest.headers[
                    'x-amz-server-side-encryption-customer-algorithm'] = 'AES256'
                rest.headers[
                    'x-amz-server-side-encryption-customer-key'] = base64.b64encode(
                    running_config['CustomerKey'])
                rest.headers[
                    'x-amz-server-side-encryption-customer-key-MD5'] = base64.b64encode(
                    hashlib.md5(running_config['CustomerKey']).digest())
            elif running_config['SrvSideEncryptType'].lower() == 'sse-kms' \
                    and running_config[
                        'SrvSideEncryptAlgorithm'].lower() == 'aws:kms':
                rest.headers['x-amz-server-side-encryption'] = 'aws:kms'
                if running_config['SrvSideEncryptAWSKMSKeyId']:
                    rest.headers[
                        'x-amz-server-side-encryption-aws-kms-key-id'] = \
                        running_config[
                            'SrvSideEncryptAWSKMSKeyId']
                if running_config['SrvSideEncryptContext']:
                    rest.headers['x-amz-server-side-encryption-context'] = \
                        running_config['SrvSideEncryptContext']
            elif running_config['SrvSideEncryptType'].lower() == 'sse-kms' \
                    and running_config[
                        'SrvSideEncryptAlgorithm'].lower() == 'aes256':
                rest.headers['x-amz-server-side-encryption'] = 'AES256'

            rest.content_length = file_tuple[1]
            md5 = ''
            if not str(file_tuple[2]).endswith('/'):
                file_location = file_tuple[2]
                md5_value = util.md5_file_encode_by_size_offset(
                    file_path=file_location,
                    size=file_tuple[1],
                    offset=0)
                md5 = md5_value.hexdigest()
                md5_encoded = str(base64.b64encode(md5_value.digest()))
                logging.debug(
                    "====== md5_value is %s, md5 value: %s,base64 value: %s.======" % (
                        md5_value, md5, md5_encoded))
                rest.headers['content-md5'] = md5_encoded
                rest.headers['x-amz-meta-md5chksum'] = md5

            retry_count = 0
            resp = obspycmd.DefineResponse()
            resp.status = '99999 Not Ready'
            while not resp.status.startswith('20'):
                resp = obspycmd.OBSRequestHandler(rest, conn).make_request(
                    file_location=file_tuple[2])
                if not resp.status.startswith('20'):
                    if retry_count == RETRY_TIMES:
                        logging.error(
                            'Max retry put_object, key: %s. Status is %s' %
                            (rest.key, resp.status))
                        retry_count += 1
                        break
                    retry_count += 1
                    logging.warn(
                        'Status is %s. Retry put_object, key: %s, retry_count: %d' %
                        (resp.status, rest.key, retry_count))
                    time.sleep(5)
                else:
                    break
            results_queue.put(
                (worker_id, user.username, rest.record_url, rest.request_type,
                 resp.start_time, resp.end_time,
                 resp.send_bytes, 0, '', resp.request_id, resp.status,
                 resp.id2))
            if retry_count > RETRY_TIMES:
                error_msg = 'FAILED:service error in PutObject'
                result_for_manifest = '%s, %s, %s, %s, %s, %s \n' % (
                    file_tuple[2], rest.bucket + '/' + rest.key,
                    rest.content_length,
                    md5, resp.e_tag, error_msg)
                logging.debug("=== the result is %s" % result_for_manifest)
                update_objects_manifest(result_for_manifest)
                continue
            # HEAD Object to compare file size with response Content-Length
            rest_head = obspycmd.OBSRequestDescriptor(
                request_type='HeadObject',
                ak=user.ak, sk=user.sk,
                auth_algorithm=running_config[
                    'AuthAlgorithm'],
                virtual_host=running_config[
                    'VirtualHost'],
                domain_name=running_config[
                    'DomainName'],
                region=running_config[
                    'Region'])
            rest_head.bucket = rest.bucket
            rest_head.key = rest.key
            rest_head.headers['Origin'] = ''
            if running_config['SrvSideEncryptType'].lower() == 'sse-c':
                rest_head.headers[
                    'x-amz-server-side-encryption-customer-algorithm'] = 'AES256'
                rest_head.headers[
                    'x-amz-server-side-encryption-customer-key'] = base64.b64encode(
                    running_config['CustomerKey'])
                rest_head.headers[
                    'x-amz-server-side-encryption-customer-key-MD5'] = base64.b64encode(
                    hashlib.md5(running_config['CustomerKey']).digest())
            resp_head = obspycmd.DefineResponse()
            resp_head.status = '99999 Not Ready'
            resp_head = obspycmd.OBSRequestHandler(rest_head,
                                                   conn).make_request()
            results_queue.put(
                (worker_id, user.username, rest_head.record_url,
                 rest_head.request_type,
                 resp_head.start_time,
                 resp_head.end_time, 0, 0, '', resp_head.request_id,
                 resp_head.status,
                 resp_head.id2))
            logging.debug(
                "=== the Content-Length is %s ===" % resp_head.content_length)
            if running_config['CompareETag'] and resp_head.content_length != \
                    file_tuple[1]:
                logging.warn(
                    "=== delete object, key: %s. Compare content-length and size error,content-length: %s ===" % (
                        rest.key, resp_head.content_length))
                rest_d = obspycmd.OBSRequestDescriptor(
                    request_type='DeleteObject',
                    ak=user.ak, sk=user.sk,
                    auth_algorithm=running_config[
                        'AuthAlgorithm'],
                    virtual_host=running_config[
                        'VirtualHost'],
                    domain_name=running_config[
                        'DomainName'],
                    region=running_config['Region'])
                rest_d.bucket = rest.bucket
                rest_d.key = rest.key
                resp_d = obspycmd.OBSRequestHandler(rest_d,
                                                    conn).make_request()
                results_queue.put(
                    (worker_id, user.username, rest_d.record_url,
                     rest_d.request_type,
                     resp_d.start_time,
                     resp_d.end_time, 0, 0, '', resp_d.request_id,
                     resp_d.status,
                     resp_d.id2))
                result_for_manifest = '%s, %s, %s, %s, %s, FAILED:Content-Length error \n' % (
                    file_tuple[2], rest.bucket + '/' + rest.key,
                    file_tuple[1],
                    md5, resp.e_tag)
                logging.debug("=== the result is %s" % result_for_manifest)
                update_objects_manifest(result_for_manifest)
                continue
            result_for_manifest = '%s, %s, %s, %s, %s, SUCCESS \n' % (
                file_tuple[2], rest.bucket + '/' + rest.key,
                file_tuple[1],
                md5, resp.e_tag)
            logging.debug("=== the result is %s" % result_for_manifest)
            update_objects_manifest(result_for_manifest)

            # if ArchiveAfterUpload is true archive the files which has been upload to bucket
            if running_config['ArchiveAfterUpload']:
                archive_dir = running_config['ArchiveDir']
                if resp.status.startswith('200'):
                    if str(file_tuple[2]).endswith('/'):
                        archive_dir = archive_dir + file_tuple[2]
                    archive_dir = archive_dir + os.path.dirname(file_tuple[2])
                    if not os.path.exists(archive_dir):
                        util.mkdir_p(archive_dir)
                        logging.warn(
                            'The ArchiveDir is no exist , mkdir %s now' % archive_dir)
                    if not str(file_tuple[2]).endswith('/'):
                        util.rename(file_tuple[2], archive_dir)
                        logging.warn(
                            'The file %s archive success' % file_tuple[2])

        else:
            rest = obspycmd.OBSRequestDescriptor(request_type='',
                                                 ak=user.ak, sk=user.sk,
                                                 auth_algorithm=running_config[
                                                     'AuthAlgorithm'],
                                                 virtual_host=running_config[
                                                     'VirtualHost'],
                                                 domain_name=running_config[
                                                     'DomainName'],
                                                 region=running_config[
                                                     'Region'])
            try:
                rest.key = file_tuple[0]
            except UnicodeDecodeError:
                result_for_manifest = '%s,   , %s,   ,   , FAILED:UnicodeDecodeError' % (
                    file_tuple[2], file_tuple[1])
                logging.debug("=== the result is %s" % result_for_manifest)
                update_objects_manifest(result_for_manifest)
                logging.error('Decode error, key: ' + file_tuple[0])
                continue
            rest.bucket = running_config['BucketNameFixed']
            process_multi_parts_upload(file_tuple=file_tuple,
                                       rest=rest,
                                       conn=conn,
                                       worker_id=worker_id)


def organize_file_paths_for_download(file_name):
    downloadTarget = str(running_config['DownloadTarget'])
    filename = file_name
    if ',' not in downloadTarget:
        if str(downloadTarget).endswith('/'):
            filename = str(file_name).replace(downloadTarget,
                                              downloadTarget.split('/')[
                                                  -2] + '/')
        elif str(downloadTarget) == '':
            filename = str(file_name)
        else:
            filename = file_name.replace(file_name, file_name.split('/')[-1])
    else:
        targets = list(set(downloadTarget.split(',')))
        for target in targets:
            target = target.strip()
            if str(file_name).startswith(target):
                filename = file_name.replace(target,
                                             target.split('/')[-2] + '/')
                break
            else:
                filename = file_name.replace(file_name,
                                             file_name.split('/')[-1])
                break
    return filename


def get_object(worker_id, conn):
    while not all_objects_queue.empty():
        try:
            # （对象名，versionId，eTag，ContentLength）
            object_tuple = all_objects_queue.get(block=False)
            logging.warn('get_object tuple:' + str(object_tuple))
        except Empty:
            logging.debug(
                "Empty when getting task tuple from queue,then continue.")
            continue
        file_name = object_tuple[0].encode(SYS_ENCODING)
        logging.debug(
            "=== file_name:%s ; size:%s ===" % (file_name, object_tuple[1]))
        file_name = organize_file_paths_for_download(file_name)
        save_path_parent = running_config['SavePath']
        file_location = os.path.join(save_path_parent, file_name)
        logging.debug("=== location file path is %s ===" % file_location)
        # 如果是文件夹，则必定是空文件夹，本地创建此文件夹后跳过进行下一个队列任务
        if file_location.endswith('/'):
            if not os.path.isdir(file_location):
                try:
                    os.makedirs(file_location)
                except OSError:
                    pass
            result_for_manifest = '%s, %s, %s, %s, %s, %s \n' % (
                file_location,
                running_config['BucketNameFixed'] + '/' + file_name,
                object_tuple[3],
                '', '', 'SUCCESS')
            logging.debug("=== the result is %s" % result_for_manifest)
            update_objects_manifest(result_for_manifest)
            continue

        if object_tuple[3] < int(running_config.get('MultipartObjectSize')):
            rest = obspycmd.OBSRequestDescriptor(request_type='GetObject',
                                                 ak=user.ak, sk=user.sk,
                                                 auth_algorithm=running_config[
                                                     'AuthAlgorithm'],
                                                 virtual_host=running_config[
                                                     'VirtualHost'],
                                                 domain_name=running_config[
                                                     'DomainName'],
                                                 region=running_config[
                                                     'Region'])

            rest.bucket = running_config['BucketNameFixed']
            # 如果是多版本对象，带版本下载
            rest.key = object_tuple[0].encode(SYS_ENCODING)
            if object_tuple[1] is not None:
                rest.query_args['versionId'] = object_tuple[1].encode('utf8')
            if running_config['SrvSideEncryptType'].lower() == 'sse-c':
                rest.headers[
                    'x-amz-server-side-encryption-customer-algorithm'] = 'AES256'
                rest.headers[
                    'x-amz-server-side-encryption-customer-key'] = base64.b64encode(
                    running_config['CustomerKey'])
                rest.headers[
                    'x-amz-server-side-encryption-customer-key-MD5'] = base64.b64encode(
                    hashlib.md5(running_config['CustomerKey']).digest())
            # Compare MD5 Consistency if inconsistent,response.status 9901 data error md5
            retry_count = 0
            resp = obspycmd.DefineResponse()
            resp.status = '99999 Not Ready'
            md5 = ''
            meta_md5 = ''
            e_tag = ''
            e_tag_record = ''
            isOk = 0
            while not resp.status.startswith('20') or md5 != meta_md5:
                resp = obspycmd.OBSRequestHandler(rest, conn).make_request(
                    save_path_parent=save_path_parent,
                    file_name=file_name)
                if running_config['CompareETag']:
                    md5_value = util.md5_file_encode_by_size_offset(
                        file_location, object_tuple[3], 0)
                    md5 = md5_value.hexdigest()
                    md5_encoded = str(base64.b64encode(md5_value.digest()))
                    logging.debug(
                        "====== md5_value is %s, md5 value: %s,base64 value: %s.======" % (
                            md5_value, md5, md5_encoded))
                    e_tag = resp.e_tag
                    meta_md5 = resp.meta_md5
                e_tag_record = 'ETag: local(%s) server(%s), meta_md5: %s' % (
                    md5, e_tag, meta_md5)
                logging.debug("=== %s ===" % e_tag_record)
                if not resp.status.startswith('20'):
                    if retry_count == RETRY_TIMES:
                        isOk = 1
                        logging.error(
                            'Max retry get_object, key: %s. Status is %s' %
                            (rest.key, resp.status))
                        break
                    retry_count += 1
                    logging.warn(
                        'Status is %s. Retry get_object, key: %s, retry_count: %d' %
                        (resp.status, rest.key, retry_count))
                    time.sleep(5)
                elif md5 != meta_md5 and meta_md5 != 'None':
                    isOk = 2
                    if retry_count == RETRY_TIMES:
                        resp.status = '9901 data error md5'
                        logging.warn(
                            'Max retry get_object, delete object, key: %s. Compare md5 error, %s.' %
                            (rest.key, e_tag_record))
                        util.delete_file(file_location)
                        break
                    retry_count += 1
                    logging.warn(
                        'Compare md5 error, %s. Retry get_object, key: %s, retry_count: %d' %
                        (e_tag_record, rest.key, retry_count))
                    time.sleep(5)
                else:
                    break

            results_queue.put(
                (worker_id, user.username, rest.record_url, rest.request_type,
                 resp.start_time, resp.end_time, 0,
                 resp.recv_bytes, e_tag_record, resp.request_id, resp.status,
                 resp.id2)
            )
            error_msg = 'FAILED:service error' if isOk == 1 else 'FAILED:MD5 error' if isOk == 2 else 'SUCCESS'
            result_for_manifest = '%s, %s, %s, %s, %s, %s \n' % (
                file_location, rest.bucket + '/' + rest.key, object_tuple[1],
                md5, meta_md5, error_msg)
            logging.debug("=== the result is %s" % result_for_manifest)
            update_objects_manifest(result_for_manifest)
        else:
            rest = obspycmd.OBSRequestDescriptor(request_type='GetObject',
                                                 ak=user.ak, sk=user.sk,
                                                 auth_algorithm=running_config[
                                                     'AuthAlgorithm'],
                                                 virtual_host=running_config[
                                                     'VirtualHost'],
                                                 domain_name=running_config[
                                                     'DomainName'],
                                                 region=running_config[
                                                     'Region'])
            if running_config['SrvSideEncryptType'].lower() == 'sse-c':
                rest.headers[
                    'x-amz-server-side-encryption-customer-algorithm'] = 'AES256'

            rest.bucket = running_config['BucketNameFixed']
            rest.key = object_tuple[0].encode(SYS_ENCODING)

            if running_config['SrvSideEncryptType'].lower() == 'sse-c':
                rest.headers[
                    'x-amz-server-side-encryption-customer-key'] = base64.b64encode(
                    running_config['CustomerKey'])
                rest.headers[
                    'x-amz-server-side-encryption-customer-key-MD5'] = base64.b64encode(
                    hashlib.md5(running_config['CustomerKey']).digest())
            result_for_manifest = process_range_download(rest, object_tuple,
                                                         save_path_parent,
                                                         worker_id)
            logging.debug("=== the result is %s" % result_for_manifest)
            update_objects_manifest(result_for_manifest)


def process_range_download(rest, object_tuple, save_path_parent, worker_id):
    size_to_download = object_tuple[3]
    part_size = int(running_config['PartSize'])
    meta_md5 = []
    total_parts = size_to_download / part_size if size_to_download % part_size == 0 else size_to_download / part_size + 1
    parts_count = 0
    range_start = 0
    th_list = []
    counter = Counter()
    stop_flag_obj = ThreadsStopFlag()
    lock_t = threading.Lock()
    data_queue = Queue.Queue(1024)
    file_name = object_tuple[0].encode(SYS_ENCODING)
    file_name = organize_file_paths_for_download(file_name)
    file_path = os.path.join(save_path_parent, file_name)
    dir_path = os.path.dirname(file_path)
    logging.debug("start to range download now! [worker id:%d] [total parts:%d]" %(worker_id, total_parts))

    if not os.path.isdir(dir_path):
        try:
            os.makedirs(dir_path)
        except OSError:
            pass

    range_file_writer = RangeFileWriter(data_queue, file_path, total_parts)
    range_file_writer.daemon = True
    range_file_writer.start()

    while size_to_download:
        if stop_flag_obj.flag:
            total_data.value -= size_to_download
            break
        if counter.count > 0 and current_concurrency.value >= running_config[
            'Concurrency']:
            time.sleep(0.5)
            continue

        rest_in = deepcopy(rest)
        range_start_in = deepcopy(range_start)
        if size_to_download >= part_size:
            rest_in.headers['Range'] = 'bytes=%d-%d' % (
                range_start, range_start + (part_size - 1))
            range_start += part_size
            size_to_download -= part_size
        else:
            rest_in.headers['Range'] = 'bytes=%d-%d' % (
                range_start, object_tuple[3] - 1)
            size_to_download = 0

        def run_download_part(rest_t, range_start_t, meta_md5):
            temp_conn = None
            try:
                temp_conn = obspycmd.MyHTTPConnection(
                    host=running_config['DomainName'],
                    is_secure=running_config['IsHTTPs'],
                    ssl_version=running_config['sslVersion'],
                    timeout=running_config['ConnectTimeout'],
                    long_connection=running_config['LongConnection'],
                    conn_header=running_config['ConnectionHeader'],
                    proxy_host=running_config['ProxyHost'],
                    proxy_port=running_config['ProxyPort'],
                    proxy_username=running_config['ProxyUserName'],
                    proxy_password=running_config['ProxyPassWord']
                )
                retry_count = 0
                resp = obspycmd.DefineResponse()
                resp.status = '99999 Not Ready'
                is_last_retry = False
                md5 = ''
                e_tag_record = ''
                while not resp.status.startswith('20'):
                    if stop_flag_obj.flag:
                        logging.error(
                            'stop_flag_obj True download_part, key: %s, range start: %d. Status is %s.' %
                            (rest.key, range_start_t, resp.status))
                        break
                    if retry_count == RETRY_TIMES:
                        is_last_retry = True
                    resp = obspycmd.OBSRequestHandler(rest_t,
                                                      temp_conn).make_request(
                        is_range_download=True,
                        range_start=range_start_t,
                        part_download_queue=data_queue,
                        stop_flag_obj=stop_flag_obj,
                        is_last_retry=is_last_retry)
                    etag_remote = resp.e_tag
                    if resp.meta_md5 not in meta_md5:
                        meta_md5.append(resp.meta_md5)
                    e_tag_record = 'ETag: local(%s) server(%s), meta_md5:(%s)' % (
                        md5, etag_remote, meta_md5[0])
                    logging.debug("=== %s ===" % e_tag_record)
                    if not resp.status.startswith('20'):
                        if retry_count == RETRY_TIMES:
                            stop_flag_obj.flag = True
                            logging.error(
                                'Max retry download_part, key: %s, range start: %d. Status is %s.' %
                                (rest.key, range_start_t, resp.status))
                            break
                        retry_count += 1
                        logging.warn(
                            'Status is %s. Retry download_part, key: %s, range start: %d, retry_count: %d' %
                            (resp.status, rest.key, range_start_t, retry_count))
                        time.sleep(5)
                    else:
                        break
                results_queue.put(
                    (worker_id, user.username, rest_t.record_url,
                     rest_t.request_type, resp.start_time,
                     resp.end_time, 0, resp.recv_bytes, e_tag_record,
                     resp.request_id, resp.status, resp.id2))
            except Exception:
                stack = traceback.format_exc()
                logging.error('range_download exception stack: %s' % stack)
            finally:
                if temp_conn:
                    temp_conn.close_connection()
                lock_t.acquire()
                counter.count -= 1
                lock_t.release()

                if counter.count > 0:
                    lock.acquire()
                    current_concurrency.value -= 1
                    lock.release()

        th = threading.Thread(target=run_download_part,
                              args=(rest_in, range_start_in, meta_md5))
        th.daemon = True
        th.start()

        lock_t.acquire()
        counter.count += 1
        lock_t.release()

        if counter.count > 1:
            lock.acquire()
            current_concurrency.value += 1
            lock.release()

        th_list.append(th)

        parts_count += 1

    for th in th_list:
        th.join()
    range_file_writer.join()

    if stop_flag_obj.flag:
        result_for_manifest = '%s, %s, %s, %s, %s, %s \n' % (
            file_path, rest.bucket + '/' + rest.key, object_tuple[1], '  ',
            '  ', 'FAILED:service error')
        return result_for_manifest

    # Compare object
    md5 = meta_md5[0]
    if running_config['CompareETag']:
        md5_value = util.md5_file_encode_by_size_offset(file_path,
                                                        object_tuple[1], 0)
        md5 = md5_value.hexdigest()
        md5_encoded = str(base64.b64encode(md5_value.digest()))
        logging.debug(
            "====== md5_value is %s, md5 value: %s,base64 value: %s.======" % (
                md5_value, md5, md5_encoded))
    e_tag_record = 'ETag: local(%s) , meta_md5: %s' % (
        md5, meta_md5[0])
    logging.debug("=== %s ===" % e_tag_record)
    if md5 != meta_md5[0] and meta_md5[0] != 'None':
        logging.warn(
            "=== Compare md5 error,now to delete the location file %s ===" % file_path)
        util.delete_file(file_path)
        logging.error("=== deleted location file, Compare md5 error! ===")
        result_for_manifest = '%s, %s, %s, %s, %s, %s \n' % (
            file_path, rest.bucket + '/' + rest.key, object_tuple[1], md5,
            meta_md5[0], 'FAILED:MD5 error')
        return result_for_manifest
    result_for_manifest = '%s, %s, %s, %s, %s, %s \n' % (
        file_path, rest.bucket + '/' + rest.key, object_tuple[1], md5,
        meta_md5[0], 'SUCCESS')
    return result_for_manifest


def range_download_for_list(big_obj_tuple_to_download):
    current_concurrency.value += 1

    worker_id = 9999

    rest = obspycmd.OBSRequestDescriptor(request_type='GetObject',
                                         ak=user.ak, sk=user.sk,
                                         auth_algorithm=running_config[
                                             'AuthAlgorithm'],
                                         virtual_host=running_config[
                                             'VirtualHost'],
                                         domain_name=running_config[
                                             'DomainName'],
                                         region=running_config['Region'])
    if running_config['SrvSideEncryptType'].lower() == 'sse-c':
        rest.headers[
            'x-amz-server-side-encryption-customer-algorithm'] = 'AES256'

    save_path_parent = running_config['SavePath']
    rest.bucket = running_config['BucketNameFixed']

    for object_tuple in big_obj_tuple_to_download:
        rest.key = object_tuple[0].encode(SYS_ENCODING)

        if running_config['SrvSideEncryptType'].lower() == 'sse-c':
            rest.headers[
                'x-amz-server-side-encryption-customer-key'] = base64.b64encode(
                rest.key[-32:].zfill(32))
            rest.headers[
                'x-amz-server-side-encryption-customer-key-MD5'] = base64.b64encode(
                hashlib.md5(rest.key[-32:].zfill(32)).digest())
        process_range_download(rest, object_tuple, save_path_parent, worker_id)

    current_concurrency.value -= 1


def process_multi_parts_upload(file_tuple, rest, conn, worker_id):
    # 1. Initiate multipart upload
    object_path = file_tuple[2]
    size_to_put = file_tuple[1]
    rest.request_type = 'InitMultiUpload'
    rest.method = 'POST'
    rest.headers = {}
    rest.query_args = {}
    rest.send_content = ''
    rest.content_length = 0

    rest.headers['content-type'] = 'application/octet-stream'
    tokens = file_tuple[0].split('.')
    if len(tokens) > 1:
        suffix = tokens[-1].strip().lower()
        if suffix in CONTENT_TYPES:
            rest.headers['content-type'] = CONTENT_TYPES[suffix]

    rest.query_args['uploads'] = None
    if running_config['PutWithACL']:
        rest.headers['x-amz-acl'] = running_config['PutWithACL']
    if running_config['SrvSideEncryptType'].lower() == 'sse-c':
        rest.headers[
            'x-amz-server-side-encryption-customer-algorithm'] = 'AES256'
        rest.headers[
            'x-amz-server-side-encryption-customer-key'] = base64.b64encode(
            running_config['CustomerKey'])
        rest.headers[
            'x-amz-server-side-encryption-customer-key-MD5'] = base64.b64encode(
            hashlib.md5(running_config['CustomerKey']).digest())
    elif running_config['SrvSideEncryptType'].lower() == 'sse-kms' and \
                    running_config[
                        'SrvSideEncryptAlgorithm'].lower() == 'aws:kms':
        rest.headers['x-amz-server-side-encryption'] = 'aws:kms'
        if running_config['SrvSideEncryptAWSKMSKeyId']:
            rest.headers['x-amz-server-side-encryption-aws-kms-key-id'] = \
                running_config[
                    'SrvSideEncryptAWSKMSKeyId']
        if running_config['SrvSideEncryptContext']:
            rest.headers['x-amz-server-side-encryption-context'] = \
                running_config['SrvSideEncryptContext']
    elif running_config['SrvSideEncryptType'].lower() == 'sse-kms' and \
                    running_config[
                        'SrvSideEncryptAlgorithm'].lower() == 'aes256':
        rest.headers['x-amz-server-side-encryption'] = 'AES256'

    resp = obspycmd.OBSRequestHandler(rest, conn).make_request()
    results_queue.put(
        (worker_id, user.username, rest.record_url, rest.request_type,
         resp.start_time,
         resp.end_time, 0, 0, '', resp.request_id, resp.status, resp.id2))

    if not resp.status.startswith('20'):
        # If initiating multipart upload failed, return.
        result_for_manifest = '%s,   , %s,   ,   , FAILED:initiating multipart upload failed \n' % (
            object_path, size_to_put)
        logging.debug("=== the result is %s" % result_for_manifest)
        update_objects_manifest(result_for_manifest)
        logging.warn('key: ' + rest.key + ', initiate multipart upload failed')
        total_data.value -= size_to_put
        return

    upload_id = resp.return_data
    logging.info("upload id: %s" % upload_id)
    # 2. Upload multipart concurrently
    rest.request_type = 'UploadPart'
    rest.method = 'PUT'
    rest.headers = {}
    rest.query_args = {}
    rest.send_content = ''
    rest.query_args['uploadId'] = upload_id
    part_number = 1
    part_etags = {}
    part_index = 0
    part_size = int(running_config['PartSize'])
    th_list = []
    counter = Counter()
    stop_flag_obj = ThreadsStopFlag()
    lock_t = threading.Lock()
    while size_to_put:
        if stop_flag_obj.flag:
            total_data.value -= size_to_put
            break
        if counter.count > 0 and current_concurrency.value >= running_config[
            'Concurrency']:
            time.sleep(0.5)
            continue
        if size_to_put >= part_size:
            rest.content_length = part_size
        else:
            rest.content_length = size_to_put

        rest.query_args['partNumber'] = str(part_number)
        md5_value = util.md5_file_encode_by_size_offset(file_path=object_path,
                                                        size=rest.content_length,
                                                        offset=(part_size * (
                                                            part_number - 1)))
        md5 = md5_value.hexdigest()
        md5_encoded = str(base64.b64encode(md5_value.digest()))
        logging.debug(
            "====== part_number %s: md5 value is %s,base64 value is %s.======" % (
                part_number, md5, md5_encoded))
        rest.headers['content-md5'] = md5_encoded

        if running_config['SrvSideEncryptType'].lower() == 'sse-c':
            rest.headers[
                'x-amz-server-side-encryption-customer-algorithm'] = 'AES256'
            rest.headers[
                'x-amz-server-side-encryption-customer-key'] = base64.b64encode(
                running_config['CustomerKey'])
            rest.headers[
                'x-amz-server-side-encryption-customer-key-MD5'] = base64.b64encode(
                hashlib.md5(running_config['CustomerKey']).digest())

        def run_upload_part(rest_t, part_number_t, part_index_t):
            temp_conn = None
            try:
                temp_conn = obspycmd.MyHTTPConnection(
                    host=running_config['DomainName'],
                    is_secure=running_config['IsHTTPs'],
                    ssl_version=running_config['sslVersion'],
                    timeout=running_config['ConnectTimeout'],
                    long_connection=running_config['LongConnection'],
                    conn_header=running_config['ConnectionHeader'],
                    proxy_host=running_config['ProxyHost'],
                    proxy_port=running_config['ProxyPort'],
                    proxy_username=running_config['ProxyUserName'],
                    proxy_password=running_config['ProxyPassWord']
                )
                retry_count = 0
                resp_in = obspycmd.DefineResponse()
                resp_in.status = '99999 Not Ready'
                while not resp_in.status.startswith('20'):
                    resp_in = obspycmd.OBSRequestHandler(rest_t,
                                                         temp_conn).make_request(
                        is_part_upload=True,
                        part_index=part_index_t,
                        file_location=object_path,
                        stop_flag_obj=stop_flag_obj)

                    if not resp_in.status.startswith('20'):
                        if retry_count == RETRY_TIMES:
                            stop_flag_obj.flag = True
                            logging.error(
                                'Max retry upload_part, key: %s, part_index: %d, status: %s' %
                                (rest.key, part_index_t, resp_in.status))
                            break
                        retry_count += 1
                        logging.warn(
                            'Retry upload_part, key: %s, part_index: %d, retry_count: %d, status: %s' %
                            (rest.key, part_index_t, retry_count,
                             resp_in.status))
                        time.sleep(5)
                    else:
                        break
                results_queue.put(
                    (worker_id, user.username, rest_t.record_url,
                     rest_t.request_type, resp_in.start_time,
                     resp_in.end_time, resp_in.send_bytes, 0, '',
                     resp_in.request_id, resp_in.status,
                     resp.id2))

                if not stop_flag_obj.flag:
                    part_etags[part_number_t] = resp_in.e_tag
                else:
                    # If some part failed, inform this worker to cancel complete multipart upload.
                    part_etags[part_number_t] = False
            except Exception:
                stack = traceback.format_exc()
                logging.error('multi_parts_upload exception stack: %s' % stack)
            finally:
                if temp_conn:
                    temp_conn.close_connection()
                lock_t.acquire()
                counter.count -= 1
                lock_t.release()

                if counter.count > 0:
                    lock.acquire()
                    current_concurrency.value -= 1
                    lock.release()

        rest_in = deepcopy(rest)
        part_number_in = deepcopy(part_number)
        part_index_in = deepcopy(part_index)
        th = threading.Thread(target=run_upload_part,
                              args=(rest_in, part_number_in, part_index_in))
        th.daemon = True
        th.start()

        lock_t.acquire()
        counter.count += 1
        lock_t.release()

        if counter.count > 1:
            lock.acquire()
            current_concurrency.value += 1
            lock.release()

        th_list.append(th)

        part_number += 1
        size_to_put -= rest.content_length
        part_index += rest.content_length

    for th in th_list:
        th.join()

    # If some part failed, cancel complete multipart upload.
    for key, value in part_etags.iteritems():
        if value is False:
            logging.warn(
                'File(%s) some part failed, abort the upload task.' % object_path)
            rest_abort = obspycmd.OBSRequestDescriptor(
                request_type='AbortMultiUpload',
                ak=user.ak, sk=user.sk,
                auth_algorithm=running_config['AuthAlgorithm'],
                virtual_host=running_config['VirtualHost'],
                domain_name=running_config['DomainName'],
                region=running_config['Region'])
            rest_abort.bucket = rest.bucket
            rest_abort.key = rest.key
            rest_abort.query_args['uploadId'] = rest.query_args['uploadId']
            obspycmd.OBSRequestHandler(rest_abort, conn).make_request()
            result_for_manifest = '%s, %s, %s,   ,   , FAILED:some part upload failed \n' % (
                object_path, rest.bucket + '/' + rest.key, file_tuple[1])
            logging.debug("=== the result is %s" % result_for_manifest)
            update_objects_manifest(result_for_manifest)
            return

    # 3. Complete multipart upload
    rest.request_type = 'CompleteMultiUpload'
    rest.method = 'POST'
    rest.headers = {}
    rest.query_args = {}
    rest.content_length = 0
    rest.headers['content-type'] = 'application/xml'
    rest.query_args['uploadId'] = upload_id
    rest.send_content = '<CompleteMultipartUpload>'
    for part_index in sorted(part_etags):
        rest.send_content += '<Part><PartNumber>%d</PartNumber><ETag>%s</ETag></Part>' % (
            part_index, part_etags[part_index])
        logging.debug("=== remote part md5 is %d %s ===" % (
            part_index, part_etags[part_index]))
    rest.send_content += '</CompleteMultipartUpload>'
    resp = obspycmd.OBSRequestHandler(rest, conn).make_request()
    results_queue.put(
        (worker_id, user.username, rest.record_url, rest.request_type,
         resp.start_time,
         resp.end_time, 0, 0, '', resp.request_id, resp.status, resp.id2))
    if not resp.status.startswith('20'):
        result_for_manifest = '%s, %s, %s,   ,   , FAILED:CompleteMultiUpload Failed \n' % (
            object_path, rest.bucket + '/' + rest.key, file_tuple[1])
        logging.debug("=== the result is %s" % result_for_manifest)
        update_objects_manifest(result_for_manifest)
        return

    # HEAD Object to compare file size with response Content-Length
    rest_head = obspycmd.OBSRequestDescriptor(request_type='HeadObject',
                                              ak=user.ak, sk=user.sk,
                                              auth_algorithm=running_config[
                                                  'AuthAlgorithm'],
                                              virtual_host=running_config[
                                                  'VirtualHost'],
                                              domain_name=running_config[
                                                  'DomainName'],
                                              region=running_config[
                                                  'Region'])
    rest_head.bucket = rest.bucket
    rest_head.key = rest.key
    rest_head.headers['Origin'] = ''
    if running_config['SrvSideEncryptType'].lower() == 'sse-c':
        rest_head.headers[
            'x-amz-server-side-encryption-customer-algorithm'] = 'AES256'
        rest_head.headers[
            'x-amz-server-side-encryption-customer-key'] = base64.b64encode(
            running_config['CustomerKey'])
        rest_head.headers[
            'x-amz-server-side-encryption-customer-key-MD5'] = base64.b64encode(
            hashlib.md5(running_config['CustomerKey']).digest())
    resp_head = obspycmd.DefineResponse()
    resp_head.status = '99999 Not Ready'
    resp_head = obspycmd.OBSRequestHandler(rest_head, conn).make_request()
    results_queue.put(
        (worker_id, user.username, rest_head.record_url, rest_head.request_type,
         resp_head.start_time,
         resp_head.end_time, 0, 0, '', resp_head.request_id, resp_head.status,
         resp_head.id2))
    logging.debug("=== the Content-Length is %s ===" % resp_head.content_length)
    if running_config['CompareETag'] and resp_head.content_length != file_tuple[
        1]:
        logging.warn(
            "=== delete object, key: %s. Compare content-length and size error,content-length: %s ===" % (
                rest.key, resp_head.content_length))
        rest_d = obspycmd.OBSRequestDescriptor(request_type='DeleteObject',
                                               ak=user.ak, sk=user.sk,
                                               auth_algorithm=running_config[
                                                   'AuthAlgorithm'],
                                               virtual_host=running_config[
                                                   'VirtualHost'],
                                               domain_name=running_config[
                                                   'DomainName'],
                                               region=running_config['Region'])
        rest_d.bucket = rest.bucket
        rest_d.key = rest.key
        resp_d = obspycmd.OBSRequestHandler(rest_d, conn).make_request()
        results_queue.put(
            (worker_id, user.username, rest_d.record_url, rest_d.request_type,
             resp_d.start_time,
             resp_d.end_time, 0, 0, '', resp_d.request_id, resp_d.status,
             resp_d.id2))
        result_for_manifest = '%s, %s, %s, %s, %s, FAILED:Content-Length error \n' % (
            object_path, rest.bucket + '/' + rest.key, file_tuple[1],
            '', '')
        logging.debug("=== the result is %s" % result_for_manifest)
        update_objects_manifest(result_for_manifest)

    # 4.Set object metadata and then copy objects
    else:
        rest_copy = obspycmd.OBSRequestDescriptor(request_type='CopyObject',
                                                  ak=user.ak, sk=user.sk,
                                                  auth_algorithm=running_config[
                                                      'AuthAlgorithm'],
                                                  virtual_host=running_config[
                                                      'VirtualHost'],
                                                  domain_name=running_config[
                                                      'DomainName'],
                                                  region=running_config[
                                                      'Region'])
        rest_copy.bucket = rest.bucket
        rest_copy.key = rest.key
        md5_value = util.md5_file_encode_by_size_offset(file_path=file_tuple[2],
                                                        size=file_tuple[1],
                                                        offset=0)
        md5 = md5_value.hexdigest()
        rest_copy.headers['x-amz-meta-md5chksum'] = md5
        rest_copy.headers[
            'x-amz-copy-source'] = '/' + rest.bucket + '/' + rest.key
        rest_copy.headers['x-amz-metadata-directive'] = 'REPLACE'
        if running_config['SrvSideEncryptType'].lower() == 'sse-c':
            rest_copy.headers[
                'x-amz-server-side-encryption-customer-algorithm'] = 'AES256'
            rest_copy.headers[
                'x-amz-server-side-encryption-customer-key'] = base64.b64encode(
                running_config['CustomerKey'])
            rest_copy.headers[
                'x-amz-server-side-encryption-customer-key-MD5'] = base64.b64encode(
                hashlib.md5(running_config['CustomerKey']).digest())
        elif running_config['SrvSideEncryptType'].lower() == 'sse-kms' \
                and running_config[
                    'SrvSideEncryptAlgorithm'].lower() == 'aws:kms':
            rest_copy.headers['x-amz-server-side-encryption'] = 'aws:kms'
            if running_config['SrvSideEncryptAWSKMSKeyId']:
                rest_copy.headers[
                    'x-amz-server-side-encryption-aws-kms-key-id'] = \
                    running_config[
                        'SrvSideEncryptAWSKMSKeyId']
            if running_config['SrvSideEncryptContext']:
                rest_copy.headers['x-amz-server-side-encryption-context'] = \
                    running_config['SrvSideEncryptContext']
        elif running_config['SrvSideEncryptType'].lower() == 'sse-kms' \
                and running_config[
                    'SrvSideEncryptAlgorithm'].lower() == 'aes256':
            rest_copy.headers['x-amz-server-side-encryption'] = 'AES256'
        resp_copy = obspycmd.DefineResponse()
        resp_copy.status = '99999 Not Ready'
        logging.debug(
            "=== make request to set object metadata and then copy objects after complete multipart upload ===")
        resp_copy = obspycmd.OBSRequestHandler(rest_copy, conn).make_request()
        results_queue.put(
            (worker_id, user.username, rest_copy.record_url,
             rest_copy.request_type,
             resp_copy.start_time,
             resp_copy.end_time, 0, 0, '', resp_copy.request_id,
             resp_copy.status, resp_copy.id2))
        if resp_copy.e_tag == None and resp_copy.status.startswith('20'):
            rest_d = obspycmd.OBSRequestDescriptor(request_type='DeleteObject',
                                                   ak=user.ak, sk=user.sk,
                                                   auth_algorithm=
                                                   running_config[
                                                       'AuthAlgorithm'],
                                                   virtual_host=running_config[
                                                       'VirtualHost'],
                                                   domain_name=running_config[
                                                       'DomainName'],
                                                   region=running_config[
                                                       'Region'])
            rest_d.bucket = rest.bucket
            rest_d.key = rest.key
            logging.warn(
                "=== make request to delete object when copy object failed ===")
            resp_d = obspycmd.OBSRequestHandler(rest_d, conn).make_request()
            results_queue.put(
                (worker_id, user.username, rest_d.record_url,
                 rest_d.request_type,
                 resp_d.start_time,
                 resp_d.end_time, 0, 0, '', resp_d.request_id, resp_d.status,
                 resp_d.id2))
            result_for_manifest = '%s, %s, %s, %s, %s, FAILED:copy object error \n' % (
                object_path, rest.bucket + '/' + rest.key, file_tuple[1],
                md5, resp_copy.e_tag)
            logging.debug("=== the result is %s" % result_for_manifest)
            update_objects_manifest(result_for_manifest)
            logging.error("=== copy object failed === %s" % resp_copy.e_tag)
            return
        logging.debug(
            "=== copy object success, etag is %s ===" % resp_copy.e_tag)
        result_for_manifest = '%s, %s, %s, %s, %s, SUCCESS \n' % (
            object_path, rest.bucket + '/' + rest.key, file_tuple[1],
            md5, resp_copy.e_tag)
        logging.debug("=== the result is %s" % result_for_manifest)
        update_objects_manifest(result_for_manifest)

    # if ArchiveAfterUpload is true archive the files which has been upload to bucket
    if running_config['ArchiveAfterUpload']:
        archive_dir = running_config['ArchiveDir']
        if resp.status.startswith('200'):
            archive_dir = archive_dir + os.path.dirname(file_tuple[2])
            if not os.path.exists(archive_dir):
                util.mkdir_p(archive_dir)
                logging.warn(
                    'The ArchiveDir is no exist , mkdir %s now' % archive_dir)
            util.rename(file_tuple[2], archive_dir)
            logging.warn('The file %s archive success' % file_tuple[2])


def multi_parts_upload_for_list(big_obj_tuple_to_upload):
    current_concurrency.value += 1

    worker_id = 9999
    conn = obspycmd.MyHTTPConnection(host=running_config['DomainName'],
                                     is_secure=running_config['IsHTTPs'],
                                     ssl_version=running_config['sslVersion'],
                                     timeout=running_config['ConnectTimeout'],
                                     long_connection=running_config[
                                         'LongConnection'],
                                     conn_header=running_config[
                                         'ConnectionHeader'],
                                     proxy_host=running_config['ProxyHost'],
                                     proxy_port=running_config['ProxyPort'],
                                     proxy_username=running_config[
                                         'ProxyUserName'],
                                     proxy_password=running_config[
                                         'ProxyPassWord']
                                     )

    rest = obspycmd.OBSRequestDescriptor(request_type='',
                                         ak=user.ak, sk=user.sk,
                                         auth_algorithm=running_config[
                                             'AuthAlgorithm'],
                                         virtual_host=running_config[
                                             'VirtualHost'],
                                         domain_name=running_config[
                                             'DomainName'],
                                         region=running_config['Region'])
    rest.bucket = running_config['BucketNameFixed']
    for large_object_tuple in big_obj_tuple_to_upload:
        try:
            rest.key = large_object_tuple[0]
        except UnicodeDecodeError:
            logging.error('Decode error, key: ' + large_object_tuple[0])
            continue
        process_multi_parts_upload(file_tuple=large_object_tuple,
                                   rest=rest,
                                   conn=conn,
                                   worker_id=worker_id)

    current_concurrency.value -= 1
    conn.close_connection()


# Start worker
def start_worker(worker_id, test_case, valid_start_time, valid_end_time,
                 conn=None, call_itself=False):
    if not call_itself:
        lock.acquire()
        current_concurrency.value += 1
        lock.release()

    if not conn:
        conn = obspycmd.MyHTTPConnection(host=running_config['DomainName'],
                                         is_secure=running_config['IsHTTPs'],
                                         ssl_version=running_config[
                                             'sslVersion'],
                                         timeout=running_config[
                                             'ConnectTimeout'],
                                         long_connection=running_config[
                                             'LongConnection'],
                                         conn_header=running_config[
                                             'ConnectionHeader'],
                                         proxy_host=running_config['ProxyHost'],
                                         proxy_port=running_config['ProxyPort'],
                                         proxy_username=running_config[
                                             'ProxyUserName'],
                                         proxy_password=running_config[
                                             'ProxyPassWord']
                                         )
    if test_case != 900:
        try:
            method_to_call = globals()[TEST_CASES[test_case].split(';')[1]]
            logging.debug('method %s called ' % method_to_call.__name__)
            method_to_call(worker_id, conn)
        except KeyboardInterrupt:
            pass
        except Exception:
            stack = traceback.format_exc()
            logging.error(
                'Call method for test case %d except: %s' % (test_case, stack))
    elif test_case == 900:
        test_cases = [int(case) for case in
                      running_config['MixOperations'].split(',')]
        tmp = 0
        while tmp < running_config['MixLoopCount']:
            logging.debug("loop count: %d " % tmp)
            tmp += 1
            for case in test_cases:
                logging.debug("case %d in mix loop called " % case)
                start_worker(worker_id, case, valid_start_time, valid_end_time,
                             conn, True)

    # If call the def self(MixOperation), return and not close connection.
    if call_itself:
        return

    # Close connection for this worker
    if conn:
        conn.close_connection()

    lock.acquire()
    current_concurrency.value -= 1
    lock.release()
    logging.info('worker_id [%d] exit, set current_concurrency.value = %d' % (
        worker_id, current_concurrency.value))
    # When all concurrent workers finish, set the business end time
    if current_concurrency.value == 0:
        valid_end_time.value = time.time()
        logging.info('worker [' + str(
            worker_id) + '], exit, set valid_end_time = ' + str(
            valid_end_time.value))


def get_total_data_size():
    if running_config['Operation'].lower() == 'download':
        total_data.value = total_data_download
        return total_data
    elif running_config['Operation'].lower() == 'upload':
        total_data.value = total_data_upload
        return total_data
    else:
        return -1


# return True: pass, False: failed
def precondition():
    # Check if the user is root.
    import getpass

    # Whether the root account must be authorized to execute
    if running_config['CheckRoot'] and LOCAL_SYS.lower().startswith(
            'linux') and 'root' != getpass.getuser():
        return False, "\033[1;31;40m%s\033[0m Please run with root account other than '%s'" % (
            "[ERROR]", getpass.getuser())

    # Check if the test case is set right.
    if running_config['Testcase'] not in TEST_CASES:
        return False, "\033[1;31;40m%s\033[0m Test Case [%d] not supported" % (
            "[ERROR]", running_config['Testcase'])

    # Test connection
    if running_config['IsHTTPs'] and not running_config['sslVersion']:
        running_config['sslVersion'] = 'SSLv23'
    print 'Testing connection to %s\t' % running_config['DomainName'].ljust(20),
    sys.stdout.flush()
    test_conn = None
    try:
        test_conn = obspycmd.MyHTTPConnection(host=running_config['DomainName'],
                                              is_secure=running_config[
                                                  'IsHTTPs'],
                                              ssl_version=running_config[
                                                  'sslVersion'],
                                              timeout=60,
                                              proxy_host=running_config[
                                                  'ProxyHost'],
                                              proxy_port=running_config[
                                                  'ProxyPort'],
                                              proxy_username=running_config[
                                                  'ProxyUserName'],
                                              proxy_password=running_config[
                                                  'ProxyPassWord']
                                              )
        test_conn.create_connection()
        test_conn.connect_connection()
        ssl_ver = ''
        if running_config['IsHTTPs']:
            if util.compare_version(sys.version.split()[0], '2.7.9') < 0:
                ssl_ver = test_conn.connection.sock._sslobj.cipher()[1]
            else:
                ssl_ver = test_conn.connection.sock._sslobj.version()
            rst = '\033[1;32;40mSUCCESS  %s\033[0m'.ljust(10) % ssl_ver
        else:
            rst = '\033[1;32;40mSUCCESS\033[0m'.ljust(10)
        print rst
        logging.info(
            'connect %s success, python version: %s,  ssl_ver: %s' % (
                running_config['DomainName'], sys.version.replace('\n', ' '),
                ssl_ver))
    except Exception, data:
        logging.error(
            'Caught exception when testing connection with %s, except: %s' % (
                running_config['DomainName'], data))
        print '\033[1;31;40m%s *%s*\033[0m' % (' Failed'.ljust(8), data)
        return False, 'Check connection failed'
    finally:
        if test_conn:
            test_conn.close_connection()

    return True, 'check passed'


def generate_run_header():
    version = '-------------------obscmd: %s, Python: %s-------------------\n' % (
        VERSION, sys.version.split(' ')[0])
    logging.warn(version)
    print version
    print 'Config loaded'

    return version


def prompt_for_input(config, prompt):
    item = running_config.get(config)
    if item is None or item == '':
        try:
            return raw_input(
                'Please input {prompt} for {config}: '.format(prompt=prompt,
                                                              config=config)).strip()
        except KeyboardInterrupt:
            print '\nTool exits...'
            sys.exit()
    else:
        return item


def update_objects_manifest(result):
    with lock_re:
        logging.debug(
            "=== begin to updata manifestfile %s === " % manifest_file)
        with open(manifest_file, 'a') as f:
            f.write(result)


def run():
    global manifest_file
    version = generate_run_header()
    worker_list = []

    # Display config
    print str(running_config).replace('\'', '')
    logging.info(running_config)

    # Precondition check
    check_result, msg = precondition()
    if not check_result:
        print 'Check error, [%s] \nExit...' % msg
        sys.exit()

    msg = 'Start at %s, pid:%d. Press Ctr+C to stop. Screen Refresh Interval: 3 sec' % (
        time.strftime('%X %x %Z'), os.getpid())
    print msg
    logging.warn(msg)
    # valid_start_time: time of starting the first concurrent
    # valid_end_time: time of finishing all requests
    # current_concurrency：number of concurrent worker, -2 represents exit manually, -1 represents normally
    valid_start_time = multiprocessing.Value('d', float(sys.maxint))
    valid_end_time = multiprocessing.Value('d', float(sys.maxint))

    # Start statistic process or thread in background, for writing log, results and refresh screen print.
    results_writer = results.ResultWriter(running_config,
                                          TEST_CASES[
                                              running_config['Testcase']].split(
                                              ';')[0].split(';')[0],
                                          results_queue, get_total_data_size(),
                                          valid_start_time, valid_end_time,
                                          current_concurrency)
    results_writer.daemon = True
    results_writer.name = 'resultsWriter'
    results_writer.start()
    print 'resultWriter started, pid: %d' % results_writer.pid
    # Make resultWriter process priority higher
    os.system('renice -19 -p ' + str(results_writer.pid) + ' >/dev/null 2>&1')
    time.sleep(.2)
    manifest_file = time.strftime('result/%Y.%m.%d_%H.%M.%S',
                                  time.localtime()) + '_' + \
                    TEST_CASES[running_config['Testcase']].split(';')[0].split(
                        ';')[0] + '_manifest.txt'
    manifest_init_str = 'LocalPath, RemotePath, ObjectSize, LocalMD5, RemoteMD5, Result   \n'
    # manifest_init_str = ['LocalPath', 'RemotePath', 'ObjectSize', 'LocalMD5', 'RemoteMD5', 'Result']
    with open(manifest_file, 'w') as f:
        f.write(manifest_init_str)

    # Exit when not complete all requests
    def exit_force(signal_num, _):
        exit_msg = "\n\n\033[5;33;40m[WARN]Terminate Signal %d Received. Terminating... please wait\033[0m" % signal_num
        logging.warn('%r' % exit_msg)
        print exit_msg, '\nWaiting for all the workers exit....'
        lock.acquire()
        current_concurrency.value = -2
        lock.release()
        time.sleep(.1)
        tmp_i = 0
        for w in worker_list:
            if w.is_alive():
                if tmp_i >= 100:
                    logging.warn('force to terminate worker %s' % w.name)
                    w.terminate()
                else:
                    time.sleep(.1)
                    tmp_i += 1
                    break

        print "\033[1;32;40mWorkers exited.\033[0m Waiting results_writer exit...",
        sys.stdout.flush()
        while results_writer.is_alive():
            current_concurrency.value = -2
            tmp_i += 1
            if tmp_i > 1000:
                logging.warn(
                    'retry too many times, shutdown results_writer using terminate()')
                results_writer.terminate()
            time.sleep(.01)
        print "\n\033[1;33;40m[WARN] Terminated\033[0m\n"
        print version
        sys.exit()

    # Start concurrent business worker
    if valid_start_time.value == float(sys.maxint):
        valid_start_time.value = time.time()
    i = 0
    while i < running_config['Concurrency']:
        worker = multiprocessing.Process(target=start_worker, args=(
            i, running_config['Testcase'], valid_start_time,
            valid_end_time, None, False))
        i += 1
        worker.daemon = True
        worker.name = 'worker-%d' % i
        worker.start()
        # Set the process priority 1 higher
        os.system('renice -1 -p ' + str(worker.pid) + ' >/dev/null 2>&1')
        worker_list.append(worker)

    logging.info('All %d workers started, valid_start_time: %.3f' % (
        len(worker_list), valid_start_time.value))

    import signal
    signal.signal(signal.SIGINT, exit_force)
    signal.signal(signal.SIGTERM, exit_force)

    time.sleep(1)
    # Exit normally
    stop_mark = False
    while not stop_mark:
        time.sleep(.3)
        if running_config['RunSeconds'] and (
                        time.time() - valid_start_time.value >= running_config[
                    'RunSeconds']):
            logging.warn('time is up, exit')
            exit_force(99, None)
        for worker in worker_list:
            if worker.is_alive():
                break
            stop_mark = True
    for worker in worker_list:
        worker.join()
    # Wait for results_writer to finish
    logging.info('Waiting results_writer to exit...')
    while results_writer.is_alive():
        current_concurrency.value = -1  # inform results_writer
        time.sleep(.3)
    print "\n\033[1;33;40m[WARN] Terminated after all requests\033[0m\n"
    print version


if __name__ == '__main__':
    usage = "usage: %prog [options] arg"
    parser = OptionParser(usage, version=VERSION)
    parser.add_option("-o", "--operation", help="upload or download or copy")
    parser.add_option("-l", "--localPath",
                      help="local path of the objects what will be upload")
    parser.add_option("-r", "--remoteDir",
                      help="remote path of the objects what will be upload")
    parser.add_option("-d", "--downloadTarget",
                      help="remote path of the objects what will be download")
    parser.add_option("-s", "--savePath",
                      help="local path of the objects what will be download")
    parser.add_option("-b", "--bucketName", help="bucketName of these objects")
    parser.add_option("-A", "--AK", help="your AK")
    parser.add_option("-S", "--SK", help="your SK")
    parser.add_option("-D", "--DomainName",
                      help="domain name address of the target bucket")
    parser.add_option("-R", "--Region",
                      help="region address of the target bucket")
    parser.add_option("-c", "--SourceBucketOfCopy",
                      help="The Soruce Bucket name of copy object")
    parser.add_option("-e", "--CopyDesitenceBucketOfCopy",
                      help="The Desitence Bucket name for copy object")
    parser.add_option("-f", "--ObjectCopy",
                      help="The Object prefix or Object need to copy;Notice:  copy  object only can do it in one Region")
    (options, args) = parser.parse_args()
    if not os.path.exists('log'):
        os.mkdir('log')
    logging.config.fileConfig('logging.conf')
    logging.info('loading config...')

    read_config(options, ConfigFile.FILE_CONFIG)

    operation = running_config['Operation'].lower()

    # User's input
    running_config['BucketNameFixed'] = prompt_for_input('BucketNameFixed',
                                                         'bucket')
    bucket = running_config['BucketNameFixed']

    if operation == 'upload':
        # User's input
        running_config['LocalPath'] = prompt_for_input('LocalPath',
                                                       'directory to be uploaded')
        local_path = running_config['LocalPath']

        r = prompt_for_input('IgnoreExist',
                             'yes/no(if no, cover files already exist)')
        if running_config.get('IgnoreExist') is None or running_config.get(
                'IgnoreExist') == '':
            if r.lower() == 'yes':
                running_config['IgnoreExist'] = True
            else:
                running_config['IgnoreExist'] = False
        is_ignore_exist = running_config['IgnoreExist']

        keys_already_exist = set()
        if is_ignore_exist:
            for t in get_all_keys_in_bucket(bucket_name=bucket, ak=user.ak,
                                            sk=user.sk):
                keys_already_exist.add(t[0])

        message = 'Start to scan all files in given path...' + local_path
        print message
        logging.warn(message)
        initialize_object_name(local_path, keys_already_exist)

        message = 'Number of file(s) to put: [%d]. Total size to upload is %d(B)' % (
            int(number_of_objects_to_put),
            total_data_upload)
        print message
        logging.warn(message)

        if int(number_of_objects_to_put) < 1:
            print 'Nothing to upload...exit'
            exit()

    elif operation == 'download':
        # User's input
        if running_config.get('DownloadTarget') is None:
            running_config['DownloadTarget'] = prompt_for_input(
                'DownloadTarget',
                'target in bucket to be downloaded(leave it empty for downloading entire bucket)'
            ).strip()
        download_target = running_config['DownloadTarget']

        # User's input
        running_config['SavePath'] = prompt_for_input('SavePath', 'save path')
        if not running_config['SavePath']:
            print 'Empty SavePath, now exit...'
            exit()

        print 'Target bucket: %s\nTarget in bucket: %s' % (
            bucket, download_target)
        versions = False
        scanner = multiprocessing.Process(target=get_all_keys_in_bucket,
                                          args=(bucket, user.ak, user.sk, download_target, True, versions))
        scanner.daemon = True
        scanner.name = 'objectScanner'
        scanner.start()

    elif operation == 'copy':
        # User's input
        if not running_config['CopySrcBucket'] or not running_config['CopyDstBucket']:
            print "Please config CopySrcBucket or CopyDstBucket"
            exit()
        copy_srcbucket = running_config['CopySrcBucket']
        copy_target = running_config['CopyObjectTarget']
        copy_dstbucket = running_config['CopyDstBucket']

        print 'Program will copy object from bucket: %s to bucket: %s\n' % (
            copy_srcbucket, copy_dstbucket)
        versions = False
        scanner = multiprocessing.Process(target=get_all_keys_in_bucket,
                                          args=(copy_srcbucket, user.ak, user.sk, copy_target, True, versions))
        scanner.daemon = True
        scanner.name = 'objectScanner'
        scanner.start()

    run()
