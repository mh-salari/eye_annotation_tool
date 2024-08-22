from abc import ABC, abstractmethod

class DetectorPlugin(ABC):
    @abstractmethod
    def __init__(self):
        pass

    @abstractmethod
    def detect(self, image):
        pass

    @property
    @abstractmethod
    def name(self):
        pass
