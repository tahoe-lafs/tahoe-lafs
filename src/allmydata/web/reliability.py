
from nevow import rend, inevow, tags as T
reliability = None # might not be usable
try:
    from allmydata import reliability # requires Numeric and PIL
except ImportError:
    pass
from allmydata.web.common import getxmlfile, get_arg


DAY=24*60*60
MONTH=31*DAY
YEAR=365*DAY

def yandm(seconds):
    return "%dy.%dm" % (int(seconds/YEAR), int( (seconds%YEAR)/MONTH))

class ReliabilityTool(rend.Page):
    addSlash = True
    docFactory = getxmlfile("reliability.xhtml")

    DEFAULT_PARAMETERS = [
        ("drive_lifetime", "8Y", "time"),
        ("k", 3, "int"),
        ("R", 7, "int"),
        ("N", 10, "int"),
        ("delta", "1M", "time"),
        ("check_period", "1M", "time"),
        ("report_period", "3M", "time"),
        ("report_span", "5Y", "time"),
        ]

    def parse_time(self, s):
        if s.endswith("M"):
            return int(s[:-1]) * MONTH
        if s.endswith("Y"):
            return int(s[:-1]) * YEAR
        return int(s)

    def format_time(self, s):
        if s%YEAR == 0:
            return "%dY" % (s/YEAR)
        if s%MONTH == 0:
            return "%dM" % (s/MONTH)
        return "%d" % s

    def get_parameters(self, ctx):
        req = inevow.IRequest(ctx)
        parameters = {}
        for name,default,argtype in self.DEFAULT_PARAMETERS:
            v = get_arg(ctx, name, default)
            if argtype == "time":
                value = self.parse_time(v)
            else:
                value = int(v)
            parameters[name] = value
        return parameters

    def renderHTTP(self, ctx):
        self.parameters = self.get_parameters(ctx)
        self.results = reliability.ReliabilityModel.run(**self.parameters)
        return rend.Page.renderHTTP(self, ctx)

    def make_input(self, name, old_value):
        return T.input(name=name, type="text",
                       value=self.format_time(old_value))

    def render_forms(self, ctx, data):
        f = T.form(action=".", method="get")
        table = []
        for name, default_value, argtype in self.DEFAULT_PARAMETERS:
            old_value = self.parameters[name]
            i = self.make_input(name, old_value)
            table.append(T.tr[T.td[name+":"], T.td[i]])
        go = T.input(type="submit", value="Recompute")
        return [T.h2["Simulation Parameters:"],
                f[T.table[table], go],
                ]

    def data_simulation_table(self, ctx, data):
        for row in self.results.samples:
            yield row

    def render_simulation_row(self, ctx, row):
        (when, unmaintained_shareprobs, maintained_shareprobs,
         P_repaired_last_check_period,
         cumulative_number_of_repairs,
         cumulative_number_of_new_shares,
         P_dead_unmaintained, P_dead_maintained) = row
        ctx.fillSlots("t", yandm(when))
        ctx.fillSlots("P_repair", "%.6f" % P_repaired_last_check_period)
        ctx.fillSlots("P_dead_unmaintained", "%.6g" % P_dead_unmaintained)
        ctx.fillSlots("P_dead_maintained", "%.6g" % P_dead_maintained)
        return ctx.tag

    def render_report_span(self, ctx, row):
        (when, unmaintained_shareprobs, maintained_shareprobs,
         P_repaired_last_check_period,
         cumulative_number_of_repairs,
         cumulative_number_of_new_shares,
         P_dead_unmaintained, P_dead_maintained) = self.results.samples[-1]
        return ctx.tag[yandm(when)]

    def render_P_loss_unmaintained(self, ctx, row):
        (when, unmaintained_shareprobs, maintained_shareprobs,
         P_repaired_last_check_period,
         cumulative_number_of_repairs,
         cumulative_number_of_new_shares,
         P_dead_unmaintained, P_dead_maintained) = self.results.samples[-1]
        return ctx.tag["%.6g (%1.8f%%)" % (P_dead_unmaintained,
                                           100*P_dead_unmaintained)]

    def render_P_loss_maintained(self, ctx, row):
        (when, unmaintained_shareprobs, maintained_shareprobs,
         P_repaired_last_check_period,
         cumulative_number_of_repairs,
         cumulative_number_of_new_shares,
         P_dead_unmaintained, P_dead_maintained) = self.results.samples[-1]
        return ctx.tag["%.6g (%1.8f%%)" % (P_dead_maintained,
                                           100*P_dead_maintained)]

    def render_P_repair_rate(self, ctx, row):
        (when, unmaintained_shareprobs, maintained_shareprobs,
         P_repaired_last_check_period,
         cumulative_number_of_repairs,
         cumulative_number_of_new_shares,
         P_dead_unmaintained, P_dead_maintained) = self.results.samples[-1]
        freq = when / cumulative_number_of_repairs
        return ctx.tag["%.6g" % freq]

    def render_P_repair_shares(self, ctx, row):
        (when, unmaintained_shareprobs, maintained_shareprobs,
         P_repaired_last_check_period,
         cumulative_number_of_repairs,
         cumulative_number_of_new_shares,
         P_dead_unmaintained, P_dead_maintained) = self.results.samples[-1]
        generated_shares = cumulative_number_of_new_shares / cumulative_number_of_repairs
        return ctx.tag["%1.2f" % generated_shares]


