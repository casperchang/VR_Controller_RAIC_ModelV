# camera.py
import subprocess
import time
import cv2
import queue
from threading import Thread
import logging
import pypylon.pylon as pylon

logger = logging.getLogger(__name__)

# Note: The CSICamera and Bufferless_VideoCapture classes are included from your file
# but are not used in the final Basler-based solution. They are kept for completeness.
class CameraBase:
    def __init__(self, camera_config=None):
        self.camera_config = camera_config
    def read(self):
        raise NotImplementedError
    def get_property(self):
        raise NotImplementedError
    def check_open(self):
        raise NotImplementedError
    def start_grabbing(self):
        raise NotImplementedError
    def stop_grabbing(self):
        raise NotImplementedError
    def check_grabbing(self):
        raise NotImplementedError
    def close(self):
        raise NotImplementedError

class BaslerCamera(CameraBase):
    def __init__(self, camera_config=None):
        super().__init__(camera_config)
        self.camera = None
        self.converter = None
        self.is_camera_initialized = False
        self.initialize_camera()

    def initialize_camera(self, max_retries=3, retry_delay=2):
        for attempt in range(1, max_retries + 1):
            try:
                if self.camera is not None:
                    self.release_camera()
                
                self.camera = pylon.InstantCamera(pylon.TlFactory.GetInstance().CreateFirstDevice())
                self.camera.Open()
                
                logger.info(f"[BaslerCamera] Using device: {self.camera.GetDeviceInfo().GetModelName()}")
                
                # Set camera parameters from config if available
                if self.camera_config:
                    self.camera.ExposureTime.SetValue(self.camera_config.get("exposure_time", 3000))
                    self.camera.AcquisitionFrameRateEnable.SetValue(True)
                    self.camera.AcquisitionFrameRate.SetValue(self.camera_config.get("frame_rate", 60.0))
                    self.camera.Width.SetValue(self.camera_config.get("width", 1600))
                    self.camera.Height.SetValue(self.camera_config.get("height", 1200))
                    self.camera.Gain.SetValue(self.camera_config.get("gain", 0.0))
                    self.camera.ReverseX.SetValue(self.camera_config.get("reverse_X", False))
                    self.camera.ReverseY.SetValue(self.camera_config.get("reverse_Y", False))
                
                self.camera.BslCenterX.Execute()
                self.camera.BslCenterY.Execute()
                
                self.camera.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)

                self.converter = pylon.ImageFormatConverter()
                self.converter.OutputPixelFormat = pylon.PixelType_BGR8packed
                self.converter.OutputBitAlignment = pylon.OutputBitAlignment_MsbAligned
                
                logger.info("[BaslerCamera] Camera initialized and settings applied.")
                self.is_camera_initialized = True
                return
                
            except Exception as e:
                logger.error(f"[BaslerCamera] Attempt {attempt} to initialize camera failed: {e}")
                self.is_camera_initialized = False
                if attempt < max_retries:
                    logger.info(f"[BaslerCamera] Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    logger.error("[BaslerCamera] Max retries reached. Initialization failed.")
                    # In a real scenario, you might raise the exception here
                    # raise e 

    def release_camera(self):
        try:
            if self.camera:
                if self.camera.IsGrabbing():
                    self.camera.StopGrabbing()
                if self.camera.IsOpen():
                    self.camera.Close()
            logger.info("[BaslerCamera] Camera resources released.")
            self.is_camera_initialized = False
        except Exception as e:
            logger.error(f"[BaslerCamera] Error releasing camera resources: {e}")

    def read(self):
        if not self.is_camera_initialized or not self.camera.IsGrabbing():
            logger.warning("[BaslerCamera][read] Camera not ready or not grabbing.")
            return False, None
        try:
            grabResult = self.camera.RetrieveResult(5000, pylon.TimeoutHandling_ThrowException)
            if grabResult.GrabSucceeded():
                image = self.converter.Convert(grabResult)
                img = image.GetArray()
                grabResult.Release()
                return True, img
            else:
                logger.error("[BaslerCamera][read] Grab failed with error: " + grabResult.GetErrorDescription())
                grabResult.Release()
                return False, None
        except Exception as e:
            logger.error(f"[BaslerCamera][read] Exception during RetrieveResult: {str(e)}. Attempting to re-initialize.")
            self.initialize_camera(max_retries=10, retry_delay=2)
            return False, None

    def start_grabbing(self):
        if self.is_camera_initialized and not self.camera.IsGrabbing():
            logger.info("[BaslerCamera] Starting grab.")
            self.camera.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)
    
    def stop_grabbing(self):
        if self.is_camera_initialized and self.camera.IsGrabbing():
            logger.info("[BaslerCamera] Stopping grab.")
            self.camera.StopGrabbing()

    def check_grabbing(self):
        return self.is_camera_initialized and self.camera.IsGrabbing()

    def get_property(self):
        if not self.is_camera_initialized: return None, None, None
        w = self.camera.Width.GetValue()
        h = self.camera.Height.GetValue()
        fps = self.camera.AcquisitionFrameRate.GetValue()
        return w, h, fps
    
    def check_open(self):
        return self.is_camera_initialized and self.camera.IsOpen()

    def close(self):    
        self.release_camera()
        logger.warning(f"[BaslerCamera][close] Camera closed.")