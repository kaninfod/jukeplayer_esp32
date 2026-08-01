s = "I want to say hello"
x = 0
index = []
while True:
	try:
		y = s.index(" ",x, len(s))
		if y > x:
			print("add y")
			index.append(y)
			x = y+1
	except ValueError:
		print("Bad value!")
		print(index)
		break
