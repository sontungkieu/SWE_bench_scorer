import unittest
from parallel_sharded_pilot import Reservations, LIFETIME, RATE, TOTAL_SECONDS


class ReservationTests(unittest.TestCase):
    def test_two_full_lifetimes_are_reserved_before_allocation(self):
        b=Reservations()
        self.assertTrue(b.admit(0));self.assertTrue(b.admit(2))
        self.assertFalse(b.admit(4))
        self.assertEqual(b.history[-1]['reserved_total_seconds'],1560)

    def test_completion_releases_unused_lifetime_and_preserves_spend(self):
        b=Reservations();b.admit(0);b.admit(2)
        b.finish(0,70,True)
        self.assertTrue(b.admit(4))
        self.assertEqual(b.history[-1]['reserved_total_seconds'],1630)

    def test_never_shorten_timeout_to_fit_remaining_budget(self):
        b=Reservations();b.admit(0);b.admit(2)
        b.finish(0,700,True)
        self.assertFalse(b.admit(4))
        b.finish(2,400,True)
        self.assertFalse(b.admit(4))
        self.assertEqual(LIFETIME,780)

    def test_uncertain_termination_keeps_full_reservation_and_stops_admission(self):
        b=Reservations();b.admit(0);b.finish(0,20,False)
        self.assertEqual(b.active,{0:780})
        self.assertFalse(b.admit(2))

    def test_eight_fast_vms_fit_and_every_reservation_stays_bounded(self):
        b=Reservations()
        for i in range(8):
            self.assertTrue(b.admit(i));b.finish(i,80,True)
        self.assertTrue(all(r['reserved_total_seconds']<=TOTAL_SECONDS for r in b.history))
        self.assertAlmostEqual(RATE*TOTAL_SECONDS,1.90368)
        self.assertLess(RATE*TOTAL_SECONDS,2)


if __name__=='__main__':unittest.main()
