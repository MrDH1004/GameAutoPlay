"""시작·재개 직후 위험도 확인. 서로 다른 두 프레임의 같은 타겟만 확정한다."""
import time


class RiskGuard:
    def __init__(self, margin=1.5, enabled=True):
        self.margin = margin
        self.enabled = enabled
        self.arm()

    def arm(self, now=None):
        now = time.monotonic() if now is None else now
        self.until = now + 20 if self.enabled else 0
        self.next_at = now
        self.previous = None
        self.confirmations = 0

    def due(self, now):
        return self.enabled and self.next_at <= now <= self.until

    def observe(self, own_power, target, now):
        self.next_at = now + 1.5
        if not own_power or not target or not target.get('power') or own_power < 1000:
            self.previous = None
            self.confirmations = 0
            return False
        enemy = target['power']
        if enemy <= own_power * self.margin:
            self.until = 0
            return False
        reading = (target.get('name'), enemy, own_power, now)
        previous = self.previous
        same = previous and reading[0] == previous[0] and enemy == previous[1] and abs(own_power / previous[2] - 1) <= 0.05 and now > previous[3]
        self.confirmations = self.confirmations + 1 if same else 1
        self.previous = reading
        if self.confirmations >= 2:
            self.until = 0
            return True
        return False
