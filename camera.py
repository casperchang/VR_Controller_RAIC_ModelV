"""
This module is used to capture images from the camera.
    1. reset_camera(): Reset the camera driver to avoid the camera freeze issue
    2. CameraBase: A base class for the camera class
    2-1. CSICamera: A class used to handle the CSI camera (which need to use Bufferless_VideoCapture)
    2-2. BaslerCamera: A class used to handle the Basler camera 
    3. Bufferless_VideoCapture: A class used to handle the camera and make the frame bufferless


"""
import subprocess
import time
import cv2
import queue
from threading import Thread
import time
import logging
import random
from module.utils.upload import APIHandler
logger = logging.getLogger(__name__)

# def reset_camera():
#     """
#     Reset the camera driver (argus) to avoid the camera freeze issue
#     (However, this function seems only needed for the CSI camera)
#     (Somtimes the sudoPassword should not work)
#     """
#     sudoPassword = "user"
#     command1 = "sudo pkill -f nvargus-daemon".split()
#     command2 = "sudo systemctl restart nvargus-daemon".split()
#     cmd1 = subprocess.Popen(["echo", sudoPassword], stdout=subprocess.PIPE)
#     time.sleep(1)
#     cmd2 = subprocess.Popen(["sudo", "-S"]+ command1, stdin=cmd1.stdout, stdout=subprocess.PIPE)
#     cmd3 = subprocess.Popen(["sudo", "-S"]+ command2, stdin=cmd2.stdout, stdout=subprocess.PIPE)
#     output = cmd3.stdout.read().decode()
#     # print(f"[reset_io][Camera][{output}]")
#     logger.info(f"[reset_camera][{output}]")
#     time.sleep(1)

class CameraBase:
    def __init__(self, camera_config=None, api_handler:APIHandler=None):
        self.camera_config = camera_config
        self.api_handler = api_handler
    def read(self):
        # Capture the image from the camera
        raise NotImplementedError

    def get_property(self):
        # Get the property of the camera (width, height, fps)
        raise NotImplementedError

    def check_open(self):
        # Check if the camera is opened
        raise NotImplementedError
    
    def start_grabbing(self):
        # Start grabbing the image from the camera
        raise NotImplementedError
    
    def stop_grabbing(self):
        # Stop grabbing the image from the camera
        raise NotImplementedError

    def check_grabbing(self):
        # Check if the camera is grabbing
        raise NotImplementedError


    def close(self):
        # Close the camera
        raise NotImplementedError


# FIXME: The call_break function will not work properly, the thread can't be terminated
class Bufferless_VideoCapture:
    """
    Create the Queue to store the frame from the camera continuously
    Once the new image is captured, the previous image will be discarded
    Hence, the OD model can process the latest image.
    
    """
    def __init__(self, camera):
        self.stop_capture = 0
        self.camera = camera
        self.q = queue.Queue()
        self.thread_id = Thread(target=self._reader)
        self.thread_id.start()

    def _reader(self):
        while not self.stop_capture:
            ret, frame = self.camera.read() # self.camera is a cv2.VideoCapture object
            if not ret:
                # logger.warning(f"[Bufferless_VideoCapture][_reader] Can't get the frame")
                continue
            if not self.q.empty():
                try:
                    self.q.get_nowait()   # discard previous (unprocessed) frame
                except queue.Empty:
                    logger.exception(f"[Bufferless_VideoCapture][_reader] Queue is empty")
                    pass
            self.q.put((ret,frame))

    # Let outside function to call this function to get the image
    def read(self):
        return self.q.get()
    
    def call_break(self):
        self.stop_capture=True
        self.thread_id.join()
        # print ("camera released")
        logger.warning(f"[Bufferless_VideoCapture][call_break] Camera released")

# TODO : Need to handle the camera_config in this class
class CSICamera(CameraBase):
    def __init__(self):
        super().__init__(camera_config=None)
        self.camera = self._initialize_camera()
        self.bufferless_capture = self._activate_bufferless_capture()
        self.grabbing = True
        # self.bufferless_capture = Bufferless_VideoCapture(camera=self.camera)
    
    def _initialize_camera(self):
        # 初始化 CSI 相機的具體邏輯
        self.reset_camera()
        return cv2.VideoCapture(self._gstreamer_pipeline(), cv2.CAP_GSTREAMER)
    
    def _activate_bufferless_capture(self):
        return Bufferless_VideoCapture(camera=self.camera)

    @staticmethod
    def reset_camera():
        """
        Reset the camera driver (argus) to avoid the camera freeze issue
        (However, this function seems only needed for the CSI camera)
        (Somtimes the sudoPassword should not work)
        """
        sudoPassword = "user"

        # 使用 echo 來提供 sudo 密碼
        echo_pass = subprocess.Popen(["echo", sudoPassword], stdout=subprocess.PIPE)
        
        # pkill 命令來終止 nvargus-daemon
        pkill_command = ["sudo", "-S", "pkill", "-f", "nvargus-daemon"]
        pkill_process = subprocess.Popen(pkill_command, stdin=echo_pass.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        pkill_stdout, pkill_stderr = pkill_process.communicate()
        
        if pkill_process.returncode != 0:
            # print(f"pkill failed: {pkill_stderr.decode()}")
            logger.error(f"[CSICamera][reset_camera] pkill failed: {pkill_stderr.decode()}")
            return
        
        # 重啟 nvargus-daemon 服務
        restart_command = ["sudo", "-S", "systemctl", "restart", "nvargus-daemon"]
        restart_process = subprocess.Popen(restart_command, stdin=echo_pass.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        restart_stdout, restart_stderr = restart_process.communicate()
        
        if restart_process.returncode != 0:
            # print(f"systemctl restart failed: {restart_stderr.decode()}")
            logger.error(f"[CSICamera][reset_camera] systemctl restart failed: {restart_stderr.decode()}")
            return
        
        # print(f"[reset_io][Camera][pkill output: {pkill_stdout.decode()}]")
        # print(f"[reset_io][Camera][systemctl restart output: {restart_stdout.decode()}]")
        logger.debug(f"[CSICamera][reset_camera] pkill output: {pkill_stdout.decode()}")
        logger.debug(f"[CSICamera][reset_camera] systemctl restart output: {restart_stdout.decode()}")

        time.sleep(5)

    

    def _gstreamer_pipeline(self,
        sensor_id=0,
        capture_width=1640,
        capture_height=1232,
        display_width=960,
        display_height=1280,
        framerate=30,
        flip_method=1,
    ):
        return (
            "nvarguscamerasrc sensor-id=%d ! "
            "video/x-raw(memory:NVMM), width=(int)%d, height=(int)%d, framerate=(fraction)%d/1 ! "
            "nvvidconv flip-method=%d ! "
            "video/x-raw, width=(int)%d, height=(int)%d, format=(string)BGRx ! "
            "videoconvert ! "
            "video/x-raw, format=(string)BGR ! appsink drop=1"
            % (
                sensor_id,
                self.camera_config["width"],
                self.camera_config["height"],
                self.camera_config["frame_rate"],
                flip_method,
                display_width,
                display_height,
            )
        )
    
    # * This the psudo function that make the code consistent with the BaslerCamera
    def start_grabbing(self):
        logger.warning("[CSICamera] Start grabbing")
        self.grabbing = True
    
    def stop_grabbing(self):
        logger.warning("[CSICamera] Stop grabbing")
        self.grabbing = False

    def check_grabbing(self):
        # logger.debug("check grabbing")
        return self.grabbing
    
    def read(self):
        return self.bufferless_capture.read()

    def get_property(self):
        w = self.camera.get(cv2.CAP_PROP_FRAME_WIDTH)
        h = self.camera.get(cv2.CAP_PROP_FRAME_HEIGHT)
        fps = self.camera.get(cv2.CAP_PROP_FPS)
        # self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 0)
        return w, h, fps
    
    def check_open(self):
        return self.camera.isOpened()

    def _close_camera(self):
        self.camera.release()
        del self.camera

    def _close_bufferless_capture(self):
        self.bufferless_capture.call_break()
        del self.bufferless_capture

    def close(self):
        self._close_camera()
        self._close_bufferless_capture()
        # print("Camera released")
        logger.warning(f"[CSICamera][close] Camera released")


    def restart_camera(self):
        self.close()  # Close the current camera
        self.camera = self._initialize_camera(self.camera_type)  # Reinitialize camera
        self.bufferless_capture = self._activate_bufferless_capture()
        # print("Camera restarted")  
        logger.warning(f"[CSICamera][restart_camera] Camera restarted")
       

import pypylon.pylon as pylon
class BaslerCamera(CameraBase):
    def __init__(self, camera_config=None, api_handler:APIHandler=None):
        super().__init__(camera_config, api_handler)
        self.camera = None
        self.converter = None
        self.is_camera_initialized = False
        self.initialize_camera()

    def initialize_camera(self, max_retries=3, retry_delay=2):
        for attempt in range(1, max_retries + 1):
            try:
                if self.camera is not None:
                    self.release_camera()
                
                # 創建並打開相機
                self.camera = pylon.InstantCamera(pylon.TlFactory.GetInstance().CreateFirstDevice())
                self.camera.Open()
                
                logger.info(f"[BaslerCamera] 使用設備: {self.camera.GetDeviceInfo().GetModelName()}")
                
                # 設置相機參數
                print(f"[BaslerCamera] 設置相機參數: {self.camera_config}")
                self.camera.ExposureTime.SetValue(self.camera_config["exposure_time"])  # 曝光時間 (microsecond)
                self.camera.AcquisitionFrameRateEnable.SetValue(True)
                self.camera.AcquisitionFrameRate.SetValue(self.camera_config["frame_rate"])  # 幀率 (fps)
                self.camera.Width.SetValue(self.camera_config["width"])  # 圖像寬度
                self.camera.Height.SetValue(self.camera_config["height"])  # 圖像高度
                self.camera.BslCenterX.Execute()  # 圖像水平居中
                self.camera.BslCenterY.Execute()  # 圖像垂直居中
                self.camera.Gain.SetValue(self.camera_config["gain"])  # 增益設定
                self.camera.ReverseX.SetValue(self.camera_config["reverse_X"]) # 水平翻轉
                self.camera.ReverseY.SetValue(self.camera_config["reverse_Y"]) # 垂直翻轉
                
                # 開始連續抓取影像
                self.camera.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)

                # 設置影像格式轉換器
                self.converter = pylon.ImageFormatConverter()
                self.converter.OutputPixelFormat = pylon.PixelType_BGR8packed
                self.converter.OutputBitAlignment = pylon.OutputBitAlignment_MsbAligned
                
                logger.info("[BaslerCamera] 相機初始化並設定完成")

                self.is_camera_initialized = True
                return
                
            except Exception as e:
                logger.error(f"[BaslerCamera] 第 {attempt} 次初始化相機失敗: {e}")
                self.is_camera_initialized = False
                if attempt < max_retries:
                    logger.info(f"[BaslerCamera] 將在 {retry_delay} 秒後重試...")
                    time.sleep(retry_delay)
                else:
                    logger.error("[BaslerCamera] 已達最大重試次數，初始化/重啟失敗")
                    self.api_handler.send_message(message="相機啟動已達最大重試次數，初始化/重啟失敗", message_type="警告", level="critical")

    def release_camera(self):
        try:
            if self.camera and self.camera.IsGrabbing():
                self.camera.StopGrabbing()
            if self.camera and self.camera.IsOpen():
                self.camera.Close()
            if self.camera:
                device = self.camera.DetachDevice()
                pylon.TlFactory.GetInstance().DestroyDevice(device)
            logger.info("[BaslerCamera] 相機資源已釋放")
            self.is_camera_initialized = False
        except Exception as e:
            logger.error(f"[BaslerCamera] 釋放相機資源時出錯: {e}")

    def read(self):
        ret = False
        img = None
        logger.debug(f"[BaslerCamera][read] IsGrabbing: {self.camera.IsGrabbing()}")

        if self.camera.IsGrabbing():
            try:
                grabResult = self.camera.RetrieveResult(5000, pylon.TimeoutHandling_ThrowException)
                if grabResult.GrabSucceeded():
                    ret = True
                    image = self.converter.Convert(grabResult)
                    logger.debug(f"[BaslerCamera][read] Image size: {image.GetWidth()} x {image.GetHeight()}")
                    img = image.GetArray()
                else:
                    logger.error("[BaslerCamera][read] Grab failed")
            except Exception as e:
                logger.error(f"[BaslerCamera][read] Exception during RetrieveResult: {str(e)}")
                # print(f"[BaslerCamera][read] Exception during RetrieveResult: {str(e)}")
                self.initialize_camera(max_retries=10, retry_delay=2)
            
            finally:
                if 'grabResult' in locals() and grabResult:
                    grabResult.Release()
        else:
            logger.error("[BaslerCamera][read] Camera is not grabbing!")
            
        return ret, img

    def start_grabbing(self):
        logger.warning("Start grabbing")
        self.camera.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)
    
    def stop_grabbing(self):
        logger.warning("Stop grabbing")
        self.camera.StopGrabbing()

    def check_grabbing(self):
        # logger.debug("check grabbing")
        return self.camera.IsGrabbing()

    
    def get_property(self):
        w = self.camera.Width.GetValue()
        h = self.camera.Height.GetValue()
        fps = self.camera.AcquisitionFrameRate.GetValue()
        return w, h, fps
    
    def check_open(self):
        return self.camera.IsOpen()

    def close(self):    
        self.camera.StopGrabbing()
        self.camera.Close()
        logger.warning(f"[BaslerCamera][close] Camera released")