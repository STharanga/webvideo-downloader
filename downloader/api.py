# -*- coding:utf-8 -*-
import re
import json
from urllib.parse import unquote
import tools
from tools import XMLUtils

# Request header required to get url
def getHeaders(url):
    isBilibili = url.find('bili') > 0 or url.count('.m4s') == 2
    isIqiyi = url.find('iqiyi.com') > 0
    isMgtv = url.find('mgtv.com') > 0

    headers = {}

    if isBilibili:
        headers['referer'] = 'https://www.bilibili.com/'
    elif isIqiyi:
        headers['referer'] = 'https://www.iqiyi.com/'
    elif isMgtv:
        headers['referer'] = 'https://www.mgtv.com/'
    return headers

def parseHlsUrl(url, headers = {}):
    content = tools.getText(url, headers)
    return tools.filterHlsUrls(content, url)

# iqiyi: Parse the mpd file
def parseIqiyiMpd(content, headers = {}):
    mediaUrls = {
        'audio': [],
        'video': [],
    }
    root = XMLUtils.parse(content)
    items = XMLUtils.findall(root, 'Period/AdaptationSet/Representation')

    for item in items:
        mType = item.attrib['mimeType'].split('/')[0]
        segName = XMLUtils.findtext(item, 'BaseURL')
        clipItems = XMLUtils.findall(root, "clip_list/clip[BaseURL='%s']" % segName)

        for clip in clipItems:
            infoUrl = XMLUtils.findtext(clip, 'remote_path').replace('&amp;', '&')
            mediaInfo = json.loads(tools.getText(infoUrl, headers))
            mediaUrls[mType].append(mediaInfo['l'])

    return mediaUrls['audio'], mediaUrls['video']

# iqiyi: Parse the link of segment description information
def parseIqiyiInfoUrls(urls, headers = {}):
    print('Total Videos: %d，Getting the real link of each video' % len(urls))

    videoUrls = []
    for url in urls:
        data = json.loads(tools.getText(url, headers, timeout=10))
        videoUrls.append(data['l'])
    return videoUrls

def parseIqiyiUrl(url, realData, headers = {}):
    if realData.startswith('{'):
        data = json.loads(realData)
    else:
        data = json.loads(tools.getText(url, headers))

    program = data['data']['program']
    if type(program) == list:
        print('The server returned an error. Possible Reason: You need to use a command line proxy to download (http_proxy/https_proxy)')
        exit()

    subtitles = []
    
    filterVideos = list(filter(lambda each: each.get('m3u8'), program['video']))
    if len(filterVideos):
        content = filterVideos[0]['m3u8']

        if content.startswith('#EXTM3U'):
            videoType = 'hls'
            audioUrls, videoUrls = [], tools.filterHlsUrls(content)
        else:
            videoType = 'dash'
            audioUrls, videoUrls = parseIqiyiMpd(content, headers)
    else:
        filterVideos = list(filter(lambda each: each.get('fs'), program['video']))
        fsList = filterVideos[0]['fs']
        basePath = data['data']['dd']
        infoUrls = list(map(lambda each: basePath + each['l'], fsList))
        videoType = 'partial'
        audioUrls, videoUrls = [], parseIqiyiInfoUrls(infoUrls, headers)

    if 'stl' in program:
        defaultSrts = list(filter(lambda x: x.get('_selected'), program['stl']))
        srts = defaultSrts + list(filter(lambda x: not x.get('_selected'), program['stl']))
        basePath = data['data']['dstl']
        subtitles = [ (srt.get('_name', 'default'), basePath + srt['srt']) for srt in srts ]
    return videoType, audioUrls, videoUrls, subtitles


# Parse the link, return the parsed url and the required request header
def parseSingleUrl(url, realData = None):
    urls = url.split('|')

    isBilibili = url.find('bili') > 0 or url.count('.m4s') == 2
    isIqiyi = any(map(lambda x: url.find(x) > 0, ['iqiyi.com', 'iq.com']))

    videoType = ''
    headers = getHeaders(url)
    audioUrls = []
    videoUrls = []
    subtitles = []

    if url.find('.m3u8') > 0:
        videoType = 'hls'
        if len(urls) == 1:
            videoUrls = parseHlsUrl(url, headers)
        else:
            videoUrls = parseHlsUrl(urls[0], headers)
            subtitles = [ (unquote(urls[i*2+1]), urls[i*2+2]) for i in range(len(urls)//2) ]
    elif isBilibili and url.find('.m4s') > 0:
        videoType = 'dash'
        audioUrls, videoUrls = urls[:1], urls[1:]
    elif isIqiyi:
        videoType, audioUrls, videoUrls, subtitles = parseIqiyiUrl(url, realData, headers)
    else:
        videoType = 'partial'
        videoUrls = urls

    return videoType, headers, audioUrls, videoUrls, subtitles




# bilibili: Get all the sub-Part information
def getAllPartInfo(url):
    content = tools.getText(url, getHeaders(url))

    # Get the sub-part name and content id (cid)
    match = re.search(r'<script>window\.__INITIAL_STATE__=(.+?});.+?</script>', content)
    data = json.loads(match.group(1))
    isOpera = 'epList' in data
    pages = data['epList'] if isOpera else data['videoData']['pages']

    allPartInfo = []
    for page in pages:
        if isOpera:
            name, partUrl = page['longTitle'], re.sub(r'\d+$', str(page['id']), url)
        else:
            name, partUrl = page['part'], url + '?p=' + str(page['page'])
        allPartInfo.append({
            'cid': page['cid'],
            'name': name,
            'url': partUrl,
        })

    return allPartInfo

# bilibili: Get the video url of the specified part
def getPartUrl(partUrl, partCid, basePlayInfoUrl, sessCookie):
    def sortBandWidth(item):
        return item['id'] * (10**10) + item['bandwidth']

    headers = getHeaders(partUrl)
    headers['Cookie'] = "CURRENT_FNVAL=16"
    content = tools.getText(partUrl, headers)

    match = re.search(r'<script>window\.__playinfo__=(.+?)</script>', content)

    if match: 
        data = match.group(1)
        data = json.loads(data)['data']
    else: 
        playInfoUrl = basePlayInfoUrl + '&cid=' + str(partCid)
        headers = { 'Cookie': sessCookie }
        data = json.loads(tools.getText(playInfoUrl, headers))
        data = data.get('data', None) or data.get('result', None)

    if 'dash' in data:
        # Audio and Video Segmentation
        data = data['dash']
        data['audio'].sort(key=sortBandWidth, reverse=True)
        data['video'].sort(key=sortBandWidth, reverse=True)
        combineVideoUrl = data['audio'][0]['baseUrl'] + '|' + data['video'][0]['baseUrl']
    elif 'durl' in data:
        # Video Segmentation
        data = data['durl']
        urls = list(map(lambda each: each['url'], data))
        combineVideoUrl = '|'.join(urls)

    return combineVideoUrl

# bilibili: Parsing multiple part links
def parseMultiPartUrl(url, pRange):
    if url.find('|') != -1:
        baseUrl, basePlayInfoUrl, sessCookie = url.split('|')
    else:
        baseUrl, basePlayInfoUrl, sessCookie = url, '', ''

    baseUrl = baseUrl.split('?')[0]
    pRange = pRange.split(' ')
    startP = int(pRange[0])
    endP = int(pRange[1]) if len(pRange) > 1 else startP

    allPartInfo = getAllPartInfo(baseUrl)
    for i in range(startP - 1, endP):
        partInfo = allPartInfo[i]
        partUrl, partCid = partInfo['url'], partInfo['cid']
        combineVideoUrl = getPartUrl(partUrl, partCid, basePlayInfoUrl, sessCookie)
        partInfo['videoUrl'] = combineVideoUrl

    return startP, endP, allPartInfo