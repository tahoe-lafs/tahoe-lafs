
SOURCES = CHK-hashes.svg file-encoding1.svg file-encoding2.svg file-encoding3.svg file-encoding4.svg file-encoding5.svg file-encoding6.svg subtree1.svg

PNGS = $(patsubst %.svg,%.png,$(SOURCES))
EPSS = $(patsubst %.svg,%.eps,$(SOURCES))

.PHONY: images-png images-eps
all: $(PNGS) $(EPSS)
images-png: $(PNGS)
images-eps: $(EPSS)

%.png: %.svg
	inkscape -b white -d 90 --export-png $@ $<
%.eps: %.svg
	inkscape --export-eps $@ $<

clean:
	rm -f *.png *.eps
