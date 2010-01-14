#! /usr/bin/python

import math
from allmydata.util import statistics
from numpy import array, matrix, dot

DAY=24*60*60
MONTH=31*DAY
YEAR=365*DAY

class ReliabilityModel:
    """Generate a model of system-wide reliability, given several input
    parameters.

    This runs a simulation in which time is quantized down to 'delta' seconds
    (default is one month): a smaller delta will result in a more accurate
    simulation, but will take longer to run. 'report_span' simulated seconds
    will be run.

    The encoding parameters are provided as 'k' (minimum number of shares
    needed to recover the file) and 'N' (total number of shares generated).
    The default parameters are 3-of-10.

    The first step is to build a probability of individual drive loss during
    any given delta. This uses a simple exponential model, in which the
    average drive lifetime is specified by the 'drive_lifetime' parameter
    (default is 8 years).

    The second step is to calculate a 'transition matrix': a table of
    probabilities that shows, given A shares at the start of the delta, what
    the chances are of having B shares left at the end of the delta. The
    current code optimistically assumes all drives are independent. A
    subclass could override that assumption.

    An additional 'repair matrix' is created to show what happens when the
    Checker/Repairer is run. In the simulation, the Checker will be run every
    'check_period' seconds (default is one month), and the Repairer will be
    run if it sees fewer than 'R' shares (default 7).

    The third step is to finally run the simulation. An initial probability
    vector is created (with a 100% chance of N shares and a 0% chance of
    fewer than N shares), then it is multiplied by the transition matrix for
    every delta of time. Each time the Checker is to be run, the repair
    matrix is multiplied in, and some additional stats are accumulated
    (average number of repairs that occur, average number of shares
    regenerated per repair).

    The output is a ReliabilityReport instance, which contains a table that
    samples the state of the simulation once each 'report_period' seconds
    (defaults to 3 months). Each row of this table will contain the
    probability vector for one sample period (chance of having X shares, from
    0 to N, at the end of the period). The report will also contain other
    information.

    """

    @classmethod
    def run(klass,
            drive_lifetime=8*YEAR,
            k=3, R=7, N=10,
            delta=1*MONTH,
            check_period=1*MONTH,
            report_period=3*MONTH,
            report_span=5*YEAR,
            ):
        self = klass()

        check_period = check_period-1
        P = self.p_in_period(drive_lifetime, delta)

        decay = self.build_decay_matrix(N, P)

        repair = self.build_repair_matrix(k, N, R)

        #print "DECAY:", decay
        #print "OLD-POST-REPAIR:", old_post_repair
        #print "NEW-POST-REPAIR:", decay * repair
        #print "REPAIR:", repair
        #print "DIFF:", (old_post_repair - decay * repair)

        START = array([0]*N + [1])
        DEAD = array([1]*k + [0]*(1+N-k))
        REPAIRp = array([0]*k + [1]*(R-k) + [0]*(1+N-R))
        REPAIR_newshares = array([0]*k +
                                 [N-i for i in range(k, R)] +
                                 [0]*(1+N-R))
        assert REPAIR_newshares.shape[0] == N+1
        #print "START", START
        #print "REPAIRp", REPAIRp
        #print "REPAIR_newshares", REPAIR_newshares

        unmaintained_state = START
        maintained_state = START
        last_check = 0
        last_report = 0
        P_repaired_last_check_period = 0.0
        needed_repairs = []
        needed_new_shares = []
        report = ReliabilityReport()

        for t in range(0, report_span+delta, delta):
            # the .A[0] turns the one-row matrix back into an array
            unmaintained_state = (unmaintained_state * decay).A[0]
            maintained_state = (maintained_state * decay).A[0]
            if (t-last_check) > check_period:
                last_check = t
                # we do a check-and-repair this frequently
                need_repair = dot(maintained_state, REPAIRp)

                P_repaired_last_check_period = need_repair
                new_shares = dot(maintained_state, REPAIR_newshares)
                needed_repairs.append(need_repair)
                needed_new_shares.append(new_shares)

                maintained_state = (maintained_state * repair).A[0]

            if (t-last_report) > report_period:
                last_report = t
                P_dead_unmaintained = dot(unmaintained_state, DEAD)
                P_dead_maintained = dot(maintained_state, DEAD)
                cumulative_number_of_repairs = sum(needed_repairs)
                cumulative_number_of_new_shares = sum(needed_new_shares)
                report.add_sample(t, unmaintained_state, maintained_state,
                                  P_repaired_last_check_period,
                                  cumulative_number_of_repairs,
                                  cumulative_number_of_new_shares,
                                  P_dead_unmaintained, P_dead_maintained)

        # record one more sample at the end of the run
        P_dead_unmaintained = dot(unmaintained_state, DEAD)
        P_dead_maintained = dot(maintained_state, DEAD)
        cumulative_number_of_repairs = sum(needed_repairs)
        cumulative_number_of_new_shares = sum(needed_new_shares)
        report.add_sample(t, unmaintained_state, maintained_state,
                          P_repaired_last_check_period,
                          cumulative_number_of_repairs,
                          cumulative_number_of_new_shares,
                          P_dead_unmaintained, P_dead_maintained)

        #def yandm(seconds):
        #    return "%dy.%dm" % (int(seconds/YEAR), int( (seconds%YEAR)/MONTH))
        #needed_repairs_total = sum(needed_repairs)
        #needed_new_shares_total = sum(needed_new_shares)
        #print "at 2y:"
        #print " unmaintained", unmaintained_state
        #print " maintained", maintained_state
        #print " number of repairs", needed_repairs_total
        #print " new shares generated", needed_new_shares_total
        #repair_rate_inv = report_span / needed_repairs_total
        #print "  avg repair rate: once every %s" % yandm(repair_rate_inv)
        #print "  avg repair download: one share every %s" % yandm(repair_rate_inv/k)
        #print "  avg repair upload: one share every %s" % yandm(report_span / needed_new_shares_total)

        return report

    def p_in_period(self, avg_lifetime, period):
        """Given an average lifetime of a disk (using an exponential model),
        what is the chance that a live disk will survive the next 'period'
        seconds?"""

        # eg p_in_period(8*YEAR, MONTH) = 98.94%
        return math.exp(-1.0*period/avg_lifetime)

    def build_decay_matrix(self, N, P):
        """Return a decay matrix. decay[start_shares][end_shares] is the
        conditional probability of finishing with end_shares, given that we
        started with start_shares."""
        decay_rows = []
        decay_rows.append( [0.0]*(N+1) )
        for start_shares in range(1, (N+1)):
            end_shares = self.build_decay_row(start_shares, P)
            decay_row = end_shares + [0.0] * (N-start_shares)
            assert len(decay_row) == (N+1), len(decay_row)
            decay_rows.append(decay_row)

        decay = matrix(decay_rows)
        return decay

    def build_decay_row(self, start_shares, P):
        """Return a decay row 'end_shares'. end_shares[i] is the chance that
        we finish with i shares, given that we started with start_shares, for
        all i between 0 and start_shares, inclusive. This implementation
        assumes that all shares are independent (IID), but a more complex
        model could incorporate inter-share failure correlations like having
        two shares on the same server."""
        end_shares = statistics.binomial_distribution_pmf(start_shares, P)
        return end_shares

    def build_repair_matrix(self, k, N, R):
        """Return a repair matrix. repair[start][end]: is the conditional
        probability of the repairer finishing with 'end' shares, given that
        it began with 'start' shares (repair if fewer than R shares). The
        repairer's behavior is deterministic, so all values in this matrix
        are either 0 or 1. This matrix should be applied *after* the decay
        matrix."""
        new_repair_rows = []
        for start_shares in range(0, N+1):
            new_repair_row = [0] * (N+1)
            if start_shares < k:
                new_repair_row[start_shares] = 1
            elif start_shares < R:
                new_repair_row[N] = 1
            else:
                new_repair_row[start_shares] = 1
            new_repair_rows.append(new_repair_row)

        repair = matrix(new_repair_rows)
        return repair

class ReliabilityReport:
    def __init__(self):
        self.samples = []

    def add_sample(self, when, unmaintained_shareprobs, maintained_shareprobs,
                   P_repaired_last_check_period,
                   cumulative_number_of_repairs,
                   cumulative_number_of_new_shares,
                   P_dead_unmaintained, P_dead_maintained):
        """
        when: the timestamp at the end of the report period
        unmaintained_shareprobs: a vector of probabilities, element[S]
                                 is the chance that there are S shares
                                 left at the end of the report period.
                                 This tracks what happens if no repair
                                 is ever done.
        maintained_shareprobs: same, but for 'maintained' grids, where
                               check and repair is done at the end
                               of each check period
        P_repaired_last_check_period: a float, with the probability
                                      that a repair was performed
                                      at the end of the most recent
                                      check period.
        cumulative_number_of_repairs: a float, with the average number
                                      of repairs that will have been
                                      performed by the end of the
                                      report period
        cumulative_number_of_new_shares: a float, with the average number
                                         of new shares that repair proceses
                                         generated by the end of the report
                                         period
        P_dead_unmaintained: a float, with the chance that the file will
                             be unrecoverable at the end of the period
        P_dead_maintained: same, but for maintained grids

        """
        row = (when, unmaintained_shareprobs, maintained_shareprobs,
               P_repaired_last_check_period,
               cumulative_number_of_repairs,
               cumulative_number_of_new_shares,
               P_dead_unmaintained, P_dead_maintained)
        self.samples.append(row)
