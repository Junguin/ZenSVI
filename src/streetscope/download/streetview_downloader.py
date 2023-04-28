import os
import random
import time
import datetime
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
import warnings
import requests
import cv2
import pkg_resources
from pathlib import Path

from streetscope.download.utils.imtool import ImageTool
from streetscope.download.utils.get_pids import panoids
from streetscope.download.utils.transform_image import ImageTransformer

class StreetViewDownloader:
    def __init__(self, dir_output, gsv_api_key = None, path_pid = None, optional_image_style = ["perspective","fisheye"], log_path = "", nthreads = 5):
        Path(dir_output).mkdir(parents=True, exist_ok=True)
        self._dir_output = dir_output
        if gsv_api_key == None:
            warnings.warn("Please provide your Google Street View API key to augment metadata.")
        self._gsv_api_key = gsv_api_key
        if path_pid == None:
            warnings.warn("Please run panoids() to get the pids first before downloading the images.")
        self._path_pid = path_pid
        self._image_style = optional_image_style
        self._log_path = log_path
        self._nthreads = nthreads
        self._user_agent = self._get_ua()
        
    @property
    def dir_output(self):
        return self._dir_output    
    @dir_output.setter
    def dir_output(self,dir_output):
        self._dir_output = dir_output
        
    @property
    def gsv_api_key(self):
        return self._gsv_api_key    
    @gsv_api_key.setter
    def gsv_api_key(self,gsv_api_key):
        self._gsv_api_key = gsv_api_key

    @property
    def path_pid(self):
        return self._path_pid    
    @path_pid.setter
    def path_pid(self,path_pid):
        self._path_pid = path_pid
    
    @property
    def image_style(self):
        return self._image_style    
    @image_style.setter
    def image_style(self,image_style):
        self._image_style = image_style
        
    @property
    def log_path(self):
        return self._log_path    
    @log_path.setter
    def log_path(self,log_path):
        self._log_path = log_path
        
    @property
    def nthreads(self):
        return self._nthreads    
    @nthreads.setter
    def nthreads(self,nthreads):
        self._nthreads = nthreads
    
    @property
    def user_agent(self):
        return self._user_agent  
    
    def _get_ua(self):
        user_agent_file = pkg_resources.resource_filename('streetscope.download.utils', 'UserAgent.csv')
        UA = []
        with open(user_agent_file, 'r') as f:
            for line in f:
                ua = {"user_agent": line.strip()}
                UA.append(ua)
        return UA

    def _read_pids(self):
        pid_df = pd.read_csv(self.path_pid)
        # get unique pids as a list
        pids = pid_df.iloc[:,0].unique().tolist()
        return pids

    def _check_already(self, all_panoids):
        name_r, all_panoids_f = set(), []
        for name in os.listdir(self.dir_output):
            name_r.add(name.split(".")[0])

        for pid in all_panoids:
            if pid not in name_r:
                all_panoids_f.append(pid)
        return all_panoids_f

    def _get_nthreads_pid(self, panoids):
        # Output path for the images
        all_pid, panos = [], []
        for i in range(len(panoids)):
            if i % self.nthreads != 0 or i == 0:
                panos.append(panoids[i])
            else:
                all_pid.append(panos)
                panos = []
        return all_pid

    def _log_write(self, pids):
        with open(self.log_path, 'a+') as fw:
            for pid in pids:
                fw.write(pid+'\n')
    
    def _augment_metadata(self, df):
        def get_year_month(pid):
            url = "https://maps.googleapis.com/maps/api/streetview/metadata?pano={}&key={}".format(pid, self.gsv_api_key)
            response = requests.get(url)
            response = response.json()
            if response['status'] == 'OK':
                # get year and month from date
                try:
                    date = response['date']
                    year = date.split("-")[0]
                    month = date.split("-")[1]
                except Exception:
                    year = None
                    month = None
                return {"year": year, "month": month}
            return {"year": None, "month": None}    

        def worker(index, row):
            panoid = row['panoid']
            year_month = get_year_month(panoid)
            return index, year_month
        
        with ThreadPoolExecutor() as executor:
            futures = {executor.submit(worker, i, row): i for i, row in df.iterrows()}
            for future in as_completed(futures):
                row_index, year_month = future.result()
                df.at[row_index, 'year'] = year_month['year']
                df.at[row_index, 'month'] = year_month['month']
        return df
                    
    def _get_pids_from_csv(self, input_csv_file, closest=False, disp=False):
        def get_street_view_info(longitude, latitude):
            results = panoids(latitude, longitude, closest=closest, disp=disp)
            return results

        def worker(row):
            input_longitude = row['longitude']
            input_latitude = row['latitude']
            return (input_longitude, input_latitude), get_street_view_info(input_longitude, input_latitude)

        df = pd.read_csv(input_csv_file)
        def standardize_column_names(df):
            longitude_variants = ['longitude', 'long', 'lon', 'lng', "x"]
            latitude_variants = ['latitude', 'lat', 'lt', "y"]
            # convert all column names to lowercase
            df.columns = [col.lower() for col in df.columns]      
            for col in df.columns:
                if col in longitude_variants:
                    df.rename(columns={col: 'longitude'}, inplace=True)
                elif col in latitude_variants:
                    df.rename(columns={col: 'latitude'}, inplace=True)

            return df
        df = standardize_column_names(df)
        results = []

        with ThreadPoolExecutor() as executor:
            futures = {executor.submit(worker, row): (row['longitude'], row['latitude']) for _, row in df.iterrows()}
            for future in as_completed(futures):
                (input_longitude, input_latitude), row_results = future.result()
                for result in row_results:
                    result['input_longitude'] = input_longitude
                    result['input_latitude'] = input_latitude
                    results.append(result)

        results_df = pd.DataFrame(results)
        return results_df
    
    def get_pids(self, path_pid, lat = None, lng = None, input_csv_file = "", closest=False, disp=False, augment_metadata=False):
        if lat != None and lng != None:
            pid = panoids(lat, lng, closest=closest, disp=disp)
        elif input_csv_file != "":
            pid = self._get_pids_from_csv(input_csv_file, closest=closest, disp=disp)
        else:
            raise ValueError("Please input the lat and lng or the csv file.")
        # save the pids
        pid_df = pd.DataFrame(pid)
        if augment_metadata & (self.gsv_api_key != None):
            pid_df = self._augment_metadata(pid_df)
        elif augment_metadata & (self.gsv_api_key == None):
            raise ValueError("Please set the gsv api key by calling the gsv_api_key method.")
        pid_df.to_csv(path_pid, index=False)
        self.path_pid = path_pid
        print("The pids have been saved to {}".format(path_pid))
        
    def _transform_image(self, style, show_size):
        def run(path_input, path_output, show_size):
            img_raw = cv2.imread(path_input, cv2.IMREAD_COLOR)
            img_transform = ImageTransformer(img_raw)
            if style == "fisheye":
                img_raw = img_transform.get_fisheye()
                cv2.imwrite(path_output, img_raw)
            
            elif style == "perspective":
                thetas = [0, 90, 180, 270]
                FOV = 90
                aspects_v = (2.25, 4)
                aspects = (9, 16)

                for theta in thetas:
                    height = int(aspects_v[0] * show_size)
                    width = int(aspects_v[1] * show_size)
                    aspect_name = '%s--%s' % (aspects[0], aspects[1])
                    img_raw = img_transform.get_perspective(FOV, theta, 0, height, width)
                    path_output_raw = path_output.replace('.png', '_Direction_%s_FOV_%s_aspect_%s_raw.png' % (theta, FOV, aspect_name))
                    cv2.imwrite(path_output_raw, img_raw)

        def process_image(dir_input, dir_output, name, show_size):
            path_input = os.path.join(dir_input, name)
            path_output = os.path.join(dir_output, name.replace('jpg', 'png'))
            return path_input, path_output, show_size

        dir_input = os.path.join(self.dir_output, 'panorama')
        dir_output = os.path.join(self.dir_output, style)
        os.makedirs(dir_output, exist_ok=True)

        index = 0
        with ThreadPoolExecutor() as executor:
            futures = [executor.submit(run, *process_image(dir_input, dir_output, name, show_size)) for name in os.listdir(dir_input)]
            for future in as_completed(futures):
                index += 1
                print(f'Converting to {style}... Now: {index} / {len(futures)}')
                future.result()
    
    def download_gsv(self, zoom = 2, h_tiles = 4, v_tiles = 2, cropped = False, full = True):
        # Import tool
        tool = ImageTool()
        # Horizontal Google Street View tiles
        # zoom 3: (8, 4); zoom 5: (26, 13) zoom 2: (4, 2) zoom 1: (2, 1);4:(8,16)
        # zoom = 2
        # h_tiles = 4  # 26
        # v_tiles = 2  # 13
        # cropped = False
        # full = True
        # create a folder within self.dir_output
        panorama_output = os.path.join(self.dir_output, "panorama")
        os.makedirs(panorama_output, exist_ok=True)
        
        panoids = self._read_pids()
        panoids_rest = self._check_already(panoids)

        # random.shuffle(panoids_rest)
        task_pids, errors, img_num = [], 0, 0

        for i in range(len(panoids_rest)):
            if i%self.nthreads != 0 or i == 0:
                task_pids.append(panoids_rest[i])
            else:
                UAs = random.sample(self.user_agent, self.nthreads)
                try:
                    tool.dwl_multiple(task_pids, task_pids, self.nthreads, zoom, v_tiles, h_tiles, panorama_output, UAs, cropped, full)
                    img_num += self.nthreads
                    print(datetime.datetime.now(), "Task:", i, "/ ", len(panoids_rest),"got:",img_num, "errors:", errors, self.dir_output)

                except Exception as e:
                    print(e)
                    time.sleep(random.randint(1, 5)*0.1)
                    errors += self.nthreads
                    if self.log_path != "":
                        self._log_write(task_pids)
                task_pids = []
                
        # transform_image if optional_image_style has any length
        if len(self.image_style) > 0:
            # check if there's anything other than "perspective" and "fisheye"
            if not all(style in ["perspective", "fisheye"] for style in self.image_style):
                raise ValueError("Please input the correct image style. The correct image style should be 'perspective' or 'fisheye'.")
            # now transform the images
            for style in self.image_style:
                self._transform_image(style, show_size = 100)

if __name__ == "__main__":
    sv_downloader = StreetViewDownloader("/Users/koichiito/Desktop/test2", gsv_api_key="AIzaSyDjIBLaZ-nAWq0RIoOUQUOzCLYzMYAN2aQ")
    sv_downloader.get_pids("/Users/koichiito/Desktop/test2/pids.csv", lat = 1.342425, lng = 103.721523, augment_metadata=True) 
    sv_downloader.download_gsv()