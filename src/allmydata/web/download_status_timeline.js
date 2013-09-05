
var globals = {};

function onDataReceived(data) {
    //console.log("got data, now rendering");
    var show_misc = false;
    //delete data.misc;
    var timeline = d3.select("#timeline");
    var w = Number(timeline.style("width").slice(0,-2));
    // the SVG fills the width of the whole div, but it will extend
    // as far vertically as necessary (depends upon the data)
    var chart = timeline.append("svg:svg")
         .attr("id", "outer_chart")
         .attr("width", w)
         .attr("pointer-events", "all")
        .append("svg:g")
         .call(d3.behavior.zoom().on("zoom", pan_and_zoom))
    ;
    // this "backboard" rect lets us catch mouse events anywhere in the
    // chart, even between the bars. Without it, we only see events on solid
    // objects like bars and text, but not in the gaps between.
    chart.append("svg:rect")
        .attr("id", "outer_rect")
        .attr("width", w).attr("height", 200).attr("fill", "none");

    // but the stuff we put inside it should have some room
    w = w-50;

    // at this point we assume the data is fixed, but the zoom/pan is not

    // create the static things (those which don't exist or not exist based
    // upon the timeline data). Their locations will be adjusted later,
    // during redraw, when we know the x+y coordinates
    chart.append("svg:text")
        .attr("class", "dyhb-label")
        //.attr("x", "20px").attr("y", y)
        .attr("text-anchor", "start") // anchor at top-left
        .attr("dy", ".71em")
        .attr("fill", "black")
        .text("DYHB requests");

    chart.append("svg:text")
        .attr("class", "read-label")
        //.attr("x", "20px").attr("y", y)
        .attr("text-anchor", "start") // anchor at top-left
        .attr("dy", ".71em")
        .attr("fill", "black")
        .text("read() requests");
    chart.append("svg:text")
        .attr("class", "segment-label")
        //.attr("x", "20px").attr("y", y)
        .attr("text-anchor", "start") // anchor at top-left
        .attr("dy", ".71em")
        .attr("fill", "black")
        .text("segment() requests");
    chart.append("svg:text")
        .attr("class", "block-label")
        //.attr("x", "20px").attr("y", y)
        .attr("text-anchor", "start") // anchor at top-left
        .attr("dy", ".71em")
        .attr("fill", "black")
        .text("block() requests");
    chart.append("svg:text")
        .attr("class", "seconds-label")
        //.attr("x", w/2).attr("y", y + 35)
        .attr("text-anchor", "middle")
        .attr("fill", "black")
        .text("seconds");


    function reltime(t) {return t-data.bounds.min;}
    var last = data.bounds.max - data.bounds.min;
    //last = reltime(d3.max(data.dyhb, function(d){return d.finish_time;}));
    last = last * 1.05;
    // long downloads are likely to have too much info, start small
    if (last > 10.0)
        last = 10.0;
    // d3.time.scale() has no support for ms or us.
    var xOFF = d3.time.scale().domain([data.bounds.min, data.bounds.max])
                 .range([0,w]);
    var x = d3.scale.linear().domain([-last*0.05, last])
              .range([0,w]);
    function tx(d) { return "translate(" +x(d) + ",0)"; }
    function left(d) { return x(reltime(d.start_time)); }
    function right(d) {
        return d.finish_time ? x(reltime(d.finish_time)) : "1px";
    }
    function width(d) {
        return d.finish_time ? x(reltime(d.finish_time))-x(reltime(d.start_time)) : "1px";
    }
    function halfwidth(d) {
        if (d.finish_time)
            return (x(reltime(d.finish_time))-x(reltime(d.start_time)))/2;
        return "1px";
    }
    function middle(d) {
        if (d.finish_time)
                return (x(reltime(d.start_time))+x(reltime(d.finish_time)))/2;
            else
                return x(reltime(d.start_time)) + 1;
        }
    function color(d) { return data.server_info[d.serverid].color; }
    function servername(d) { return data.server_info[d.serverid].short; }
    function timeformat(duration) {
        // TODO: trim to microseconds, maybe humanize
        return duration;
    }

    function zoomin() {
        //console.log("zoom in!");
        //console.log(x.domain());
        var old = x.domain();
        var quarterwidth = (old[1] - old[0])/4;
        x.domain([old[0]+quarterwidth, old[1]-quarterwidth]);
        //console.log(x.domain());
        redraw();
        //d3.event.preventDefault();
    }

    function zoomout() {
        //console.log("zoom out!");
        var old = x.domain();
        var halfwidth = (old[1] - old[0])/2;
        x.domain([old[0]-halfwidth, old[1]+halfwidth]);
        redraw();
    }

    function pan_and_zoom() {
        //console.log("P", x.domain());
        if (d3.event) d3.event.transform(x);
        //console.log("p", x.domain());
        redraw();
    }

    function clip() {
        var clipped = {};
        var min = data.bounds.min + x.domain()[0];
        var max = data.bounds.min + x.domain()[1];
        function inside(d) {
            var finish_time = d.finish_time || d.start_time;
            if (Math.max(d.start_time, min) < Math.min(finish_time, max))
                return true;
            return false;
        }
        clipped.dyhb = data.dyhb.filter(inside);
        clipped.read = data.read.filter(inside);
        clipped.segment = data.segment.filter(inside);
        clipped.block = data.block.filter(inside);
        if (show_misc && data.misc)
            clipped.misc = data.misc.filter(inside);
        return clipped;
    }

    function redraw() {
        // at this point zoom/pan must be fixed
        var clipped = clip(data);

        var y = 0;
        //chart.select(".dyhb-label")
        //    .attr("x", x(0))//"20px")
        //    .attr("y", y);
        y += 20;

        // DYHB section
        var dyhb_y = d3.scale.ordinal()
                        .domain(d3.range(data.dyhb.length))
                        .rangeBands([y, y+data.dyhb.length*20], .2);
        y += data.dyhb.length*20;
        var dyhb = chart.selectAll("g.dyhb") // one per row
             .data(clipped.dyhb, function(d) { return d.start_time; })
             .attr("transform", function(d,i) {
                       return "translate("+x(reltime(d.start_time))+","
                           +dyhb_y(i)+")";
                   });
        var new_dyhb = dyhb.enter().append("svg:g")
             .attr("class", "dyhb")
             .attr("transform", function(d,i) {
                       return "translate("+x(reltime(d.start_time))+","
                           +dyhb_y(i)+")";
                   })
        ;
        dyhb.exit().remove();
        dyhb.select("rect")
             .attr("width", width)
        ;
        new_dyhb.append("svg:rect")
             .attr("width", width)
             .attr("height", dyhb_y.rangeBand())
             .attr("stroke", "black")
             .attr("fill", color)
             .attr("title", function(d){return "shnums: "+d.response_shnums;})
        ;
        new_dyhb.append("svg:text")
             .attr("text-anchor", "end")
             .attr("fill", "#444")
             .attr("x", "-0.3em") // for some reason dx doesn't work
             .attr("dy", "1.0em")
             .attr("font-size", "12px")
             .text(servername)
        ;
        dyhb.select(".rightbox")
             .attr("transform", function(d) {return "translate("+width(d)
                                             +",0)";});
        var dyhb_rightboxes = new_dyhb.append("svg:g")
             .attr("class", "rightbox")
             .attr("transform", function(d) {return "translate("+width(d)
                                             +",0)";})
        ;
        dyhb_rightboxes.append("svg:text")
             .attr("text-anchor", "start")
             .attr("y", dyhb_y.rangeBand())
             .attr("dx", "0.5em")
             .attr("dy", "-0.4em")
             .attr("fill", "#444")
             .attr("font-size", "14px")
             .text(function (d) {return "shnums: "+d.response_shnums;})
        ;

        // read() requests
        chart.select(".read-label")
            .attr("x", "20px")
            .attr("y", y);
        y += 20;
        var read = chart.selectAll("g.read")
             .data(clipped.read, function(d) { return d.start_time; })
             .attr("transform", function(d) {
                       return "translate("+left(d)+","+(y+30*d.row)+")"; });
        read.select("rect")
             .attr("width", width);
        read.select("text")
             .attr("x", halfwidth);
        var new_read = read.enter().append("svg:g")
             .attr("class", "read")
             .attr("transform", function(d) {
                       return "translate("+left(d)+","+(y+30*d.row)+")"; })
        ;
        read.exit().remove();
        y += 30*(1+d3.max(data.read, function(d){return d.row;}));
        new_read.append("svg:rect")
             .attr("width", width)
             .attr("height", 20)
             .attr("fill", "red")
             .attr("stroke", "black")
             .attr("title", function(d)
                   {return "read(start="+d.start+", len="+d.length+") -> "
                    +d.bytes_returned+" bytes";})
        ;
        new_read.append("svg:text")
             .attr("x", halfwidth)
             .attr("dy", "0.9em")
             .attr("fill", "black")
             .text(function(d) {return "["+d.start+":+"+d.length+"]";})
        ;

        // segment requests
        chart.select(".segment-label")
            .attr("x", "20px")
            .attr("y", y);
        y += 20;
        var segment = chart.selectAll("g.segment")
             .data(clipped.segment, function(d) { return d.start_time; })
             .attr("transform", function(d) {
                       return "translate("+left(d)+","+(y+30*d.row)+")"; });
        segment.select("rect")
             .attr("width", width);
        segment.select("text")
             .attr("x", halfwidth);
        var new_segment = segment.enter().append("svg:g")
             .attr("class", "segment")
             .attr("transform", function(d) {
                       return "translate("+left(d)+","+(y+30*d.row)+")"; })
        ;
        segment.exit().remove();
        y += 30*(1+d3.max(data.segment, function(d){return d.row;}));
        new_segment.append("svg:rect")
             .attr("width", width)
             .attr("height", 20)
             .attr("fill", function(d){return d.success ? "#cfc" : "#fcc";})
             .attr("stroke", "black")
             .attr("stroke-width", 1)
             .attr("title", function(d) {
                       return "seg"+d.segment_number+" ["+d.segment_start
                           +":+"+d.segment_length+"] (took "
                           +timeformat(d.finish_time-d.start_time)+")";})
        ;
        new_segment.append("svg:text")
             .attr("x", halfwidth)
             .attr("text-anchor", "middle")
             .attr("dy", "0.9em")
             .attr("fill", "black")
             .text(function(d) {return d.segment_number;})
        ;

        var shnum_colors = d3.scale.category10();

        if (show_misc && "misc" in clipped) {
            // misc requests
            var misc_label = chart.select("text.misc-label");
            if (!misc_label.node()) {
                chart.append("svg:text")
                    .attr("class", "misc-label");
                misc_label = chart.select("text.misc-label");
            }
            misc_label
                .attr("text-anchor", "start") // anchor at top-left
                .attr("dy", ".71em")
                .attr("fill", "black")
                .text("misc requests")
                .attr("x", "20px")
                .attr("y", y)
            ;
            y += 20;
            var misc = chart.selectAll("g.misc")
                .data(clipped.misc, function(d) { return d.start_time; })
                .attr("transform", function(d) {
                          return "translate("+left(d)+","+(y+30*d.row)+")"; });
            misc.select("rect")
                .attr("width", width);
            misc.select("text")
                .attr("x", halfwidth);
            var new_misc = misc.enter().append("svg:g")
                .attr("class", "misc")
                .attr("transform", function(d) {
                          return "translate("+left(d)+","+(y+30*d.row)+")"; })
            ;
            misc.exit().remove();
            y += 30*(1+d3.max(data.misc, function(d){return d.row;}));
            new_misc.append("svg:rect")
                .attr("width", width)
                .attr("height", 20)
                .attr("fill", "white")
                .attr("stroke", "black")
                .attr("stroke-width", 1)
                .attr("title", function(d) {
                          return d.what+" (took "
                              +timeformat(d.finish_time-d.start_time)+")";})
            ;
            new_misc.append("svg:text")
                .attr("x", halfwidth)
                .attr("text-anchor", "middle")
                .attr("dy", "0.9em")
                .attr("fill", "black")
                .text(function(d) {return d.what;})
            ;
        } else {
            chart.select("text.misc-label").remove();
            chart.selectAll("g.misc").remove();
        }
        // block requests
        chart.select(".block-label")
            .attr("x", "20px")
            .attr("y", y);
        y += 20;
        var block_row_to_y = {};
        var dummy = function() {
            var row_y=y;
            for (var group=0; group < data.block_rownums.length; group++) {
                for (var row=0; row < data.block_rownums[group]; row++) {
                    block_row_to_y[group+"-"+row] = row_y;
                    row_y += 12; y += 12;
                }
                row_y += 5; y += 5;
            }
        }();
        var blocks = chart.selectAll("g.block")
             .data(clipped.block, function(d) { return d.start_time; })
             .attr("transform", function(d) {
                       var ry = block_row_to_y[d.row[0]+"-"+d.row[1]];
                       return "translate("+left(d)+"," +ry+")"; });
        blocks.select("rect")
            .attr("width", width);
        blocks.select("text")
            .attr("x", halfwidth);
        var new_blocks = blocks.enter().append("svg:g")
             .attr("class", "block")
             .attr("transform", function(d) {
                       var ry = block_row_to_y[d.row[0]+"-"+d.row[1]];
                       return "translate("+left(d)+"," +ry+")"; })
        ;
        blocks.exit().remove();
        // everything appended to blocks starts at the top-left of the
        // correct per-rect location
        new_blocks.append("svg:rect")
             .attr("width", width)
             .attr("y", function(d) {return (d.response_length > 100) ? 0:3;})
             .attr("height",
                   function(d) {return (d.response_length > 100) ? 10:4;})
             .attr("fill", color)
             .attr("stroke", function(d){return shnum_colors(d.shnum);})
             .attr("stroke-width", 1)
             .attr("title", function(d){
                       return "sh"+d.shnum+"-on-"+d.serverid.slice(0,4)
                           +" ["+d.start+":+"+d.length+"] -> "
                           +d.response_length;})
        ;
        new_blocks.append("svg:text")
             .attr("x", halfwidth)
             .attr("dy", "0.9em")
             .attr("fill", "black")
             .attr("font-size", "8px")
             .attr("text-anchor", "middle")
             .text(function(d) {return "sh"+d.shnum;})
        ;

        var num = d3.format(".4g");

        // horizontal scale markers: vertical lines at rational timestamps
        var rules = chart.selectAll("g.rule")
            .data(x.ticks(10))
            .attr("transform", tx);
        rules.select("text").text(x.tickFormat(10));

        var newrules = rules.enter().insert("svg:g")
              .attr("class", "rule")
              .attr("transform", tx)
        ;

        newrules.append("svg:line")
            .attr("class", "rule-tick")
            .attr("stroke", "black");
        chart.selectAll("line.rule-tick")
            .attr("y1", y)
            .attr("y2", y + 6);
        newrules.append("svg:line")
            .attr("class", "rule-red")
            .attr("stroke", "red")
            .attr("stroke-opacity", .3);
        chart.selectAll("line.rule-red")
            .attr("y1", 0)
            .attr("y2", y);
        newrules.append("svg:text")
            .attr("class", "rule-text")
            .attr("dy", ".71em")
            .attr("text-anchor", "middle")
            .attr("fill", "black")
            .text(x.tickFormat(10));
        chart.selectAll("text.rule-text")
            .attr("y", y + 9);
        rules.exit().remove();
        chart.select(".seconds-label")
            .attr("x", w/2)
            .attr("y", y + 35);
        y += 45;

        d3.select("#outer_chart").attr("height", y);
        d3.select("#outer_rect").attr("height", y);
        d3.select("#zoom").attr("transform", "translate("+(w-10)+","+10+")");
    }
    globals.x = x;
    globals.redraw = redraw;

    d3.select("#zoom_in_button").on("click", zoomin);
    d3.select("#zoom_out_button").on("click", zoomout);
    d3.select("#toggle_misc_button").on("click",
                                        function() {
                                            show_misc = !show_misc;
                                            redraw();
                                        });
    d3.select("#reset_button").on("click",
                                  function() {
                                      x.domain([-last*0.05, last]).range([0,w]);
                                      redraw();
                                      });

    redraw();
}

$(function() {
      var datafile = "event_json";
      if (location.hash)
          datafile = location.hash.slice(1);
      //console.log("fetching data from "+datafile);
      $.ajax({url: datafile,
              method: 'GET',
              dataType: 'json',
              success: onDataReceived });
});

