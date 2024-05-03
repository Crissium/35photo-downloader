#!/usr/bin/env python3

import json
import os
import re
import requests
import string
import sys
from bs4 import BeautifulSoup
from functools import partial
from threading import Thread
from queue import Queue


print_stderr = partial(print, file=sys.stderr)
next_endpoint = 'https://35photo.pro/show_block.php'
re_rss_user_id = re.compile(r'https://35photo.pro/rss/user_(\d+).xml')
re_path_banned_chars = re.compile(r'[\\/:*?"<>|]')
headers = {
	'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0',
	'Accept': '*/*',
	'Accept-Language': 'en-GB,en;q=0.5',
	'X-Requested-With': 'XMLHttpRequest',
	'DNT': '1',
	'Connection': 'keep-alive',
	'Sec-Fetch-Dest': 'empty',
	'Sec-Fetch-Mode': 'cors',
	'Sec-Fetch-Site': 'same-origin',
}


class Downloader(Thread):
	def __init__(self, photos: Queue, quiet: bool) -> None:
		super().__init__()
		self._photos = photos
		self._quiet = quiet

	def run(self) -> None:
		while True:
			title, url = self._photos.get()
			if title is None and url is None:
				self._photos.task_done()
				break
			
			filename = title + '.jpg' # 35photo.pro only serves jpg images
			if os.path.isfile(filename):
				if not self._quiet:
					print_stderr(f'{filename} already exists, skipping')
				return
			# while os.path.isfile(filename):
			# 	print_stderr(f'File {filename} already exists, saving as ', end='')
			# 	filename, ext = os.path.splitext(filename)
			# 	filename += '_dup' + ext
			# 	print_stderr(filename)

			response = requests.get(url, headers=headers)
			if response.status_code == 200:
				with open(filename, 'wb') as f:
					f.write(response.content)
			else:
				print_stderr(f'Failed to download {filename} from {url} - {response.status_code}')
			
			self._photos.task_done()


def get_photos_of_series(series_url: str) -> list[str]:
	response = requests.get(series_url, headers=headers)
	if response.status_code != 200:
		print_stderr(f'Failed to get {series_url} - {response.status_code}')
		return []
	
	soup = BeautifulSoup(response.text, 'lxml')
	container = soup.find('div', class_='containerMain')
	script = container.find('script').string
	data_begin_pos = script.find('photoData = ') + len('photoData = ')
	data_end_pos = script.find(';\n', data_begin_pos)
	data = json.loads(script[data_begin_pos:data_end_pos])
	return [item['src'] for item in data['series']]


def get_photos_of_block(soup: BeautifulSoup, photos: Queue) -> str: # last id
	for a in soup.find_all('a', class_='item'):
		photo_url = a['href-large']
		photo_id = a['photo-id']
		title = a.img['title']
		if ' - ' in title:
			title = title.split(' - ')[1]
			title = re_path_banned_chars.sub(' ', title).strip()
			title = ' '.join(title.split())
			if all(ch in string.punctuation for ch in title)\
				or (len(title) > 1 and all(ch == title[0] for ch in title)):
				title = photo_id
			else:
				title = f'{photo_id} {title}'
		else:
			title = photo_id
		
		if 'series' in a['class']:
			urls = get_photos_of_series(a['href'])
			for i, url in enumerate(urls):
				photos.put((f'{title} ({i+1:02})', url))
		else:
			photos.put((title, photo_url))
	
	return photo_id


def main(username: str, quiet: bool) -> None:
	base = f'https://35photo.pro/{username}'
	response = requests.get(base, headers=headers)
	if response.status_code != 200:
		print_stderr(f'Failed to get {base} - {response.status_code}')
		return
	
	soup_base = BeautifulSoup(response.text, 'lxml')
	
	# Determine the user ID from the RSS link
	rss_link = soup_base.find('a', href=re_rss_user_id)
	user_id = re_rss_user_id.search(rss_link['href']).group(1)

	photos = Queue()

	workers = [Downloader(photos, quiet) for _ in range(8)]
	for worker in workers:
		worker.start()

	last_id = get_photos_of_block(soup_base, photos)
	while True:
		response = requests.get(
			next_endpoint,
			headers=headers,
			params={
			'type': 'getNextPageData',
			'page': 'photoUser',
			'lastId': last_id,
			'user_id': user_id
			}
		)

		if response.status_code != 200:
			print_stderr(f'Failed to fetch next page - {response.status_code}, aborting.')
			break
		response = response.json()
		if response['data'] == '':
			break

		soup = BeautifulSoup(response['data'], 'lxml')
		last_id = get_photos_of_block(soup, photos)

	# Signal the workers to stop
	for _ in workers:
		photos.put((None, None))

	photos.join()


if __name__ == '__main__':
	if len(sys.argv) != 2:
		print_stderr(f'Usage: {sys.argv[0]} <username> [-q|--quiet]')
		sys.exit(1)
	
	quiet = (len(sys.argv) == 3 and (sys.argv[2] == '-q' or sys.argv[2] == '--quiet'))
	main(sys.argv[1], quiet)
