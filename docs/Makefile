
SOURCES = subtree1.svg lease-tradeoffs.svg

PNGS = $(patsubst %.svg,%.png,$(SOURCES))
EPSS = $(patsubst %.svg,%.eps,$(SOURCES))

.PHONY: images-png images-eps
all: $(PNGS) $(EPSS)
images-png: $(PNGS)
images-eps: $(EPSS)

%.png: %.svg
	inkscape -b white -d 90 -D --export-png $@ $<
%.eps: %.svg
	inkscape --export-eps $@ $<

%.html: %.rst
	rst2html.py $< $@

clean:
	rm -f *.png *.eps
