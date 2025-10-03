#!/usr/bin/env python3
import sys
import datetime


def clear_screen():
	import os
	os.system('clear' if os.name == 'posix' else 'cls')


class VCardBuilder:
	def __init__(self):
		self.props = []  # list of (name, params_dict, value)
		self.version = "4.0"

	def add_property(self, name: str, value: str, params: dict | None = None):
		name = name.strip().upper()
		if not name:
			return
		self.props.append((name, params or {}, value))

	def remove_property(self, index: int):
		if 0 <= index < len(self.props):
			self.props.pop(index)

	def list_properties(self):
		if not self.props:
			print("(no properties yet)")
			return
		for i, (name, params, value) in enumerate(self.props):
			param_str = ";".join([f"{k}={v}" for k, v in params.items()])
			print(f"{i+1}. {name}{(';' + param_str) if param_str else ''}:{value}")

	def render(self) -> str:
		lines = [
			"BEGIN:VCARD",
			f"VERSION:{self.version}"
		]
		for name, params, value in self.props:
			param_str = ";".join([f"{k}={v}" for k, v in params.items()])
			lines.append(f"{name}{(';' + param_str) if param_str else ''}:{value}")
		lines.append("END:VCARD")
		return "\n".join(lines)


def input_nonempty(prompt: str) -> str:
	while True:
		val = input(prompt).strip()
		if val:
			return val
		print("❌ Value is required.")


def add_standard_fields(vb: VCardBuilder):
	print("\nAdd Standard Fields")
	print("-" * 20)
	print("Leave blank to skip any field.")
	fn = input("Full Name (FN): ").strip()
	if fn:
		vb.add_property("FN", fn)
	# N: Surname;Given;Additional;Prefixes;Suffixes
	surname = input("Last Name (Surname): ").strip()
	given = input("First Name (Given): ").strip()
	additional = input("Additional Names (optional): ").strip()
	prefixes = input("Honorific Prefixes (optional): ").strip()
	suffixes = input("Honorific Suffixes (optional): ").strip()
	if any([surname, given, additional, prefixes, suffixes]):
		vb.add_property("N", f"{surname};{given};{additional};{prefixes};{suffixes}")
	org = input("Organisation (ORG): ").strip()
	if org:
		vb.add_property("ORG", org)
	title = input("Title (TITLE): ").strip()
	if title:
		vb.add_property("TITLE", title)
	email = input("Primary Email (EMAIL): ").strip()
	if email:
		vb.add_property("EMAIL", email, {"TYPE": "work"})
	phone = input("Primary Phone (TEL): ").strip()
	if phone:
		vb.add_property("TEL", phone, {"TYPE": "cell,voice"})
	url = input("Website (URL): ").strip()
	if url:
		vb.add_property("URL", url, {"TYPE": "home"})
	addr = input("Address (one-line ADR, components separated by semicolons): ").strip()
	if addr:
		# Expecting P.O. Box;Extended;Street;Locality;Region;PostalCode;Country
		vb.add_property("ADR", addr, {"TYPE": "home"})
	bday = input("Birthday (YYYYMMDD or YYYY-MM-DD): ").strip()
	if bday:
		bval = bday.replace("-", "")
		vb.add_property("BDAY", bval)


def add_social_field(vb: VCardBuilder):
	print("\nAdd Social/Profile Field")
	print("-" * 25)
	plat = input_nonempty("Platform (e.g., github, instagram, linkedin): ")
	url = input_nonempty("Profile URL: ")
	vb.add_property("X-SOCIALPROFILE", url, {"TYPE": plat})


def add_arbitrary_field(vb: VCardBuilder):
	print("\nAdd Arbitrary Field")
	print("-" * 20)
	name = input_nonempty("Property name (e.g., NOTE, IMPP, PHOTO, KEY, X-CUSTOM): ").upper()
	params = {}
	print("Enter parameters as key=value (e.g., TYPE=work). Blank line to stop.")
	while True:
		line = input().strip()
		if not line:
			break
		if "=" not in line:
			print("Invalid param. Use key=value.")
			continue
		k, v = [p.strip() for p in line.split("=", 1)]
		params[k.upper()] = v
	value = input_nonempty("Value: ")
	vb.add_property(name, value, params)


def add_multiple_contact(vb: VCardBuilder):
	print("\nAdd Additional Emails/Phones")
	print("-" * 30)
	while True:
		choice = input("Add [1] Email, [2] Phone, [3] Done: ").strip()
		if choice == "1":
			em = input_nonempty("Email: ")
			type_hint = input("Type (work/home/other, optional): ").strip() or "other"
			vb.add_property("EMAIL", em, {"TYPE": type_hint})
		elif choice == "2":
			ph = input_nonempty("Phone: ")
			type_hint = input("Type (cell/work/home/other, optional): ").strip() or "cell"
			vb.add_property("TEL", ph, {"TYPE": type_hint})
		elif choice == "3":
			break
		else:
			print("Invalid choice.")


def copy_to_clipboard(text: str) -> bool:
	import os, subprocess, platform
	sysname = platform.system().lower()
	try:
		if 'darwin' in sysname:
			p = subprocess.Popen(['pbcopy'], stdin=subprocess.PIPE)
			p.communicate(text.encode('utf-8'))
			return p.returncode == 0
		elif 'linux' in sysname:
			# Try xclip, then xsel
			if subprocess.call(['which', 'xclip'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
				p = subprocess.Popen(['xclip', '-selection', 'clipboard'], stdin=subprocess.PIPE)
				p.communicate(text.encode('utf-8'))
				return p.returncode == 0
			elif subprocess.call(['which', 'xsel'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
				p = subprocess.Popen(['xsel', '--clipboard', '--input'], stdin=subprocess.PIPE)
				p.communicate(text.encode('utf-8'))
				return p.returncode == 0
		elif 'windows' in sysname:
			p = subprocess.Popen(['clip'], stdin=subprocess.PIPE, shell=True)
			p.communicate(text.encode('utf-8'))
			return p.returncode == 0
	except Exception:
		return False
	return False


def paste_from_clipboard() -> str | None:
	import subprocess, platform
	sysname = platform.system().lower()
	try:
		if 'darwin' in sysname:
			return subprocess.check_output(['pbpaste']).decode('utf-8')
		elif 'linux' in sysname:
			if subprocess.call(['which', 'xclip'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
				return subprocess.check_output(['xclip', '-selection', 'clipboard', '-o']).decode('utf-8')
			elif subprocess.call(['which', 'xsel'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
				return subprocess.check_output(['xsel', '--clipboard', '--output']).decode('utf-8')
		elif 'windows' in sysname:
			return subprocess.check_output(['powershell', '-command', 'Get-Clipboard']).decode('utf-8')
	except Exception:
		return None
	return None


def parse_vcard_into_builder(vb: VCardBuilder, text: str):
	lines = [l.strip() for l in text.splitlines() if l.strip()]
	in_vcard = False
	props = []
	for line in lines:
		if line.upper() == 'BEGIN:VCARD':
			in_vcard = True
			continue
		if line.upper() == 'END:VCARD':
			break
		if not in_vcard:
			continue
		# Split into name(;params...):value
		if ':' not in line:
			continue
		head, value = line.split(':', 1)
		parts = head.split(';')
		name = parts[0].upper().strip()
		params = {}
		for p in parts[1:]:
			if '=' in p:
				k, v = p.split('=', 1)
				params[k.strip().upper()] = v.strip()
			else:
				params[p.strip().upper()] = ''
		props.append((name, params, value))
	# Replace builder props
	vb.props = props


def edit_properties(vb: VCardBuilder):
	while True:
		clear_screen()
		print("🛠️ Edit Properties")
		print("-" * 20)
		vb.list_properties()
		print("\n[e <num>] Edit  [d <num>] Delete  [a] Add new  [q] Done")
		cmd = input("> ").strip()
		if cmd == 'q' or cmd == '':
			break
		if cmd == 'a':
			add_arbitrary_field(vb)
			continue
		if cmd.startswith('e '):
			idx_str = cmd[2:].strip()
			if idx_str.isdigit():
				i = int(idx_str) - 1
				if 0 <= i < len(vb.props):
					name, params, value = vb.props[i]
					new_name = input(f"Name [{name}]: ").strip() or name
					print("Enter params key=value, blank to stop. Current:", ";".join([f"{k}={v}" for k, v in params.items()]))
					new_params = {}
					while True:
						ln = input()
						if not ln:
							break
						if '=' in ln:
							k, v = [p.strip() for p in ln.split('=', 1)]
							new_params[k.upper()] = v
						else:
							print("Invalid param; use key=value")
					new_value = input(f"Value [{value}]: ").strip() or value
					vb.props[i] = (new_name.upper(), new_params, new_value)
				else:
					print("Invalid index")
			else:
				print("Invalid command")
		elif cmd.startswith('d '):
			idx_str = cmd[2:].strip()
			if idx_str.isdigit():
				i = int(idx_str) - 1
				vb.remove_property(i)
			else:
				print("Invalid command")
		else:
			print("Unknown command")
			input("Press Enter...")


def main():
	vb = VCardBuilder()
	while True:
		clear_screen()
		print("📇 vCard Builder (v4.0)")
		print("=" * 30)
		print("1. Add Standard Fields")
		print("2. Add Social/Profile Field")
		print("3. Add Additional Emails/Phones")
		print("4. Add Arbitrary Field")
		print("5. List Current Properties")
		print("6. Remove Property by Index")
		print("7. Preview vCard")
		print("8. Save vCard to File")
		print("9. Copy vCard to Clipboard")
		print("10. Paste vCard from Clipboard")
		print("11. Paste vCard (Manual Paste)")
		print("12. Edit Properties")
		print("13. Exit")
		choice = input("\nEnter your choice: ").strip()
		if choice == "1":
			clear_screen()
			add_standard_fields(vb)
			input("\nPress Enter to continue...")
		elif choice == "2":
			clear_screen()
			add_social_field(vb)
			input("\nPress Enter to continue...")
		elif choice == "3":
			clear_screen()
			add_multiple_contact(vb)
			input("\nPress Enter to continue...")
		elif choice == "4":
			clear_screen()
			add_arbitrary_field(vb)
			input("\nPress Enter to continue...")
		elif choice == "5":
			clear_screen()
			print("Current Properties:\n")
			vb.list_properties()
			input("\nPress Enter to continue...")
		elif choice == "6":
			idx_raw = input("Index to remove (1-based): ").strip()
			if idx_raw.isdigit():
				vb.remove_property(int(idx_raw) - 1)
			else:
				print("Invalid index.")
			input("\nPress Enter to continue...")
		elif choice == "7":
			clear_screen()
			vc = vb.render()
			print("Preview:\n")
			print(vc)
			input("\nPress Enter to continue...")
		elif choice == "8":
			filename = input("Filename (e.g., contact.vcf): ").strip() or "contact.vcf"
			with open(filename, "w", encoding="utf-8") as f:
				f.write(vb.render() + "\n")
			print(f"Saved to {filename}")
			input("\nPress Enter to continue...")
		elif choice == "9":
			vc = vb.render()
			if copy_to_clipboard(vc):
				print("✅ vCard copied to clipboard")
			else:
				print("❌ Could not copy to clipboard (install xclip/xsel on Linux)")
			input("\nPress Enter to continue...")
		elif choice == "10":
			p = paste_from_clipboard()
			if p:
				parse_vcard_into_builder(vb, p)
				print("✅ vCard pasted from clipboard")
			else:
				print("❌ Could not read from clipboard")
			input("\nPress Enter to continue...")
		elif choice == "11":
			print("Paste vCard content below. End with an empty line.")
			buf = []
			while True:
				line = input()
				if not line:
					break
				buf.append(line)
			parse_vcard_into_builder(vb, "\n".join(buf))
			print("✅ vCard parsed")
			input("\nPress Enter to continue...")
		elif choice == "12":
			edit_properties(vb)
		elif choice == "13":
			print("👋 Goodbye!")
			break
		else:
			print("Invalid choice.")
			input("\nPress Enter to continue...")


if __name__ == "__main__":
	try:
		main()
	except KeyboardInterrupt:
		print("\n\n👋 Goodbye!")

