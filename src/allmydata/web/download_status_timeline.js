
$(function() {

      function onDataReceived(data) {
          var bounds = { min: data.bounds.min,
                         max: data.bounds.max
                       };
          //bounds.max = data.dyhb[data.dyhb.length-1].finish_time;
          var duration = bounds.max - bounds.min;
          var WIDTH = 600;
          var vis = new pv.Panel().canvas("timeline").margin(30);

          var dyhb_top = 0;
          var read_top = dyhb_top + 30*data.dyhb[data.dyhb.length-1].row+60;
          var segment_top = read_top + 30*data.read[data.read.length-1].row+60;
          var block_top = segment_top + 30*data.segment[data.segment.length-1].row+60;
          var block_row_to_y = {};
          var row_y=0;
          for (var group=0; group < data.block_rownums.length; group++) {
              for (var row=0; row < data.block_rownums[group]; row++) {
                  block_row_to_y[group+"-"+row] = row_y;
                  row_y += 10;
              }
              row_y += 5;
          }

          var height = block_top + row_y;
          var kx = bounds.min;
          var ky = 1;
          var x = pv.Scale.linear(bounds.min, bounds.max).range(0, WIDTH-40);
          var relx = pv.Scale.linear(0, duration).range(0, WIDTH-40);
          //var y = pv.Scale.linear(-ky,ky).range(0, height);
          //x.nice(); relx.nice();

          /* add the invisible panel now, at the bottom of the stack, so that
          it won't steal mouseover events and prevent tooltips from
          working. */
          var zoomer = vis.add(pv.Panel)
              .events("all")
              .event("mousedown", pv.Behavior.pan())
              .event("mousewheel", pv.Behavior.zoom())
              .event("pan", transform)
              .event("zoom", transform)
          ;

          vis.anchor("top").top(-20).add(pv.Label).text("DYHB Requests");

          vis.add(pv.Bar)
              .data(data.dyhb)
              .height(20)
              .top(function (d) {return 30*d.row;})
              .left(function(d){return x(d.start_time);})
              .width(function(d){return x(d.finish_time)-x(d.start_time);})
              .title(function(d){return "shnums: "+d.response_shnums;})
              .fillStyle(function(d){return data.server_info[d.serverid].color;})
              .strokeStyle("black").lineWidth(1);

          vis.add(pv.Rule)
              .data(data.dyhb)
              .top(function(d){return 30*d.row + 20/2;})
              .left(0).width(0)
              .strokeStyle("#888")
              .anchor("left").add(pv.Label)
              .text(function(d){return d.serverid.slice(0,4);});

          /* we use a function for data=relx.ticks() here instead of
           simply .data(relx.ticks()) so that it will be recalculated when
           the scales change (by pan/zoom) */
          var xaxis = vis.add(pv.Rule)
              .data(function() {return relx.ticks();})
              .strokeStyle("#ccc")
              .left(relx)
              .anchor("bottom").add(pv.Label)
              .text(function(d){return relx.tickFormat(d)+"s";});

          var read = vis.add(pv.Panel).top(read_top);
          read.anchor("top").top(-20).add(pv.Label).text("read() requests");

          read.add(pv.Bar)
              .data(data.read)
              .height(20)
              .top(function (d) {return 30*d.row;})
              .left(function(d){return x(d.start_time);})
              .width(function(d){return x(d.finish_time)-x(d.start_time);})
              .title(function(d){return "read(start="+d.start+", len="+d.length+") -> "+d.bytes_returned+" bytes";})
              .fillStyle("red")
              .strokeStyle("black").lineWidth(1);

          var segment = vis.add(pv.Panel).top(segment_top);
          segment.anchor("top").top(-20).add(pv.Label).text("segment() requests");

          segment.add(pv.Bar)
              .data(data.segment)
              .height(20)
              .top(function (d) {return 30*d.row;})
              .left(function(d){return x(d.start_time);})
              .width(function(d){return x(d.finish_time)-x(d.start_time);})
              .title(function(d){return "seg"+d.segment_number+" ["+d.segment_start+":+"+d.segment_length+"] (took "+(d.finish_time-d.start_time)+")";})
              .fillStyle(function(d){if (d.success) return "#c0ffc0";
                                    else return "#ffc0c0";})
              .strokeStyle("black").lineWidth(1);

          var block = vis.add(pv.Panel).top(block_top);
          block.anchor("top").top(-20).add(pv.Label).text("block() requests");

          var shnum_colors = pv.Colors.category10();
          block.add(pv.Bar)
              .data(data.block)
              .height(10)
              .top(function (d) {return block_row_to_y[d.row[0]+"-"+d.row[1]];})
              .left(function(d){return x(d.start_time);})
              .width(function(d){return x(d.finish_time)-x(d.start_time);})
              .title(function(d){return "sh"+d.shnum+"-on-"+d.serverid.slice(0,4)+" ["+d.start+":+"+d.length+"] -> "+d.response_length;})
              .fillStyle(function(d){return data.server_info[d.serverid].color;})
              .strokeStyle(function(d){return shnum_colors(d.shnum).color;})
              .lineWidth(function(d)
                         {if (d.response_length > 100) return 3;
                         else return 1;
                          })
          ;


          vis.height(height);

          function zoomin() {
              var t = zoomer.transform().invert();
              t.k = t.k/1.5;
              zoomer.transform(t.invert());
              zoompan(t);
          }

          function zoomout() {
              var t = zoomer.transform().invert();
              t.k = t.k*1.5;
              zoomer.transform(t.invert());
              zoompan(t);
          }

          function transform() {
              var t = this.transform().invert();
              zoompan(t);
          }
          function zoompan(t) {
              // when t.x=0 and t.k=1.0, left should be bounds.min
              x.domain(bounds.min + (t.x/WIDTH)*duration,
                       bounds.min + t.k*duration + (t.x/WIDTH)*duration);
              relx.domain(0 + t.x/WIDTH*duration,
                          t.k*duration + (t.x/WIDTH)*duration);
              vis.render();
          }

          vis.render();
          $("#zoomin").click(zoomin);
          $("#zoomout").click(zoomout);
      }

      $.ajax({url: "event_json",
              method: 'GET',
              dataType: 'json',
              success: onDataReceived });
});

