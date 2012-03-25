import re

re_parts = re.compile(r"\((?P<part>\d+)/(?P<total_parts>\d+)\)")
re_yenc = re.compile(r"yEnc")
re_size = re.compile(r"(?P<size>\d+KB)")

# selecting the filename, perferring to grab everything inside of quotes. some posters do not use
# quotes and don't consistently delimit what is filename and what is descriptive subject.
re_filename = re.compile(r"\"??(?P<filename>[^\"]*?(\.part\d{1,2}\.rar|\.vol\d{1,2}\+\d{1,2}|\.cbr|\.cbz|\.pdf|\.rar|\.par2|\.par|\.zip|\.sfv|\.nfo|\.nzb)+)\"??", re.I)
re_filename_wo_quotes = re.compile("")
re_similar = re.compile(r"\)(\d+)/\d+\(")
re_x_of_y = re.compile(r"\d{1,3} of \d{1,3} - ")

with open('bad.txt', 'r') as f:
	lines = f.readlines()
	re_bad = []

	for line in f.readlines():
		re_bad.append(re.compile(line, re.I))

def subject_to_totals(subject):

	matches = re_parts.search(subject)
	if matches:
		return (matches.group("part"), matches.group("total_parts"))
	else:
		return (None, None)

def subject_to_yenc(subject):

	matches = re.search(r"yEnc", subject)
	if matches: return True
	else: return False

def subject_to_size(subject):

	matches = re_size.search(subject)
	if matches: 
		return matches.group("size")
	else: 
		return None

def subject_to_filename(subject):

	matches = re_filename.search(subject)
	if matches:
		# remove any text such as "1 of 8"
		return re_x_of_y.sub("", matches.group("filename")).strip()
	else:
		return None

def subject_to_similar(subject):
	# my trick to subtitute right to left by reversing the string, make the substitution,
	# then reverse back.
	re.sub
	return re_similar.sub(")/1(", subject[::-1], 1)[::-1]

def bad_filter(text):
	for r in re_bad:
		if r.search(text):
			return True
	return False


if __name__ == "__main__":
	assert(subject_to_filename('(1/17) "Heavy Metal Vol.9.rar" - 713.49 MB - Heavy Metal (Complete Comic) Vol.9 yEnc (1/131)') == 
		"Heavy Metal Vol.9.rar")
	assert(subject_to_filename('(1/17) "Heavy Metal Vol.9.part1.rar" - 713.49 MB - Heavy Metal (Complete Comic) Vol.9 yEnc (1/261)') ==
		"Heavy Metal Vol.9.part1.rar")
	
	assert(subject_to_filename('(Kroost 6) [4/7] - "Kroost 6.cbr.vol01+2.PAR2" yEnc (1/)') ==
		"Kroost 6.cbr.vol01+2.PAR2")
	assert(subject_to_filename("(I'M BA-A-A-A-ACK!!!!) Kull The Destroyer #25 ||1978 || FBScan || [4/5] - \"Kull The Destroyer 025 (1978) (FBScan).CBR.vol1+2.PAR2\"") ==
		"Kull The Destroyer 025 (1978) (FBScan).CBR.vol1+2.PAR2")
			

	