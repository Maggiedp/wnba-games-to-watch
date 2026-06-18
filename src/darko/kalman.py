"""Scalar Kalman filter for one player's latent talent (run separately for offense
and defense). Daily driver = box signal (update); slow structural anchor = RAPM
(via x0); drift = aging curve (predict). q = process noise, r = observation noise."""


class KalmanFilter:
    def __init__(self, x0: float, p0: float, q: float, r: float):
        self.x = float(x0)  # state estimate
        self.p = float(p0)  # state variance
        self.q = float(q)  # process noise
        self.r = float(r)  # observation noise

    def predict(self, drift: float = 0.0) -> float:
        self.x = self.x + drift
        self.p = self.p + self.q
        return self.x

    def update(self, z: float) -> float:
        k = self.p / (self.p + self.r)
        self.x = self.x + k * (z - self.x)
        self.p = (1 - k) * self.p
        return self.x
