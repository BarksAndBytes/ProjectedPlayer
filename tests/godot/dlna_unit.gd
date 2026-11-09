extends SceneTree
## Checks the DLNA renderer's parsing helpers. Prints "FAIL ..." for each failure and
## "DONE <failures>" at the end. Run by tests/test_dlna.py.

const R := preload("res://addons/projected_player/dlna/dlna_renderer.gd")
const Ssdp := preload("res://addons/projected_player/dlna/ssdp.gd")
const Upnp := preload("res://addons/projected_player/dlna/upnp_services.gd")

var failures := 0


func check(ok: bool, what: String) -> void:
	if not ok:
		failures += 1
		print("FAIL " + what)


func _initialize() -> void:
	check(R.format_time(0) == "0:00:00", "format 0")
	check(R.format_time(3725.9) == "1:02:05", "format 1:02:05")
	check(R.parse_time("0:00:05") == 5.0, "parse 0:00:05")
	check(R.parse_time("01:02:03") == 3723.0, "parse 01:02:03")
	check(is_equal_approx(R.parse_time("0:00:02.500"), 2.5), "parse fraction")
	check(is_equal_approx(R.parse_time("0:00:02.1/4"), 2.25), "parse F0/F1")
	check(R.parse_time("5") < 0.0 and R.parse_time("0:61:00") < 0.0 and R.parse_time("a:b:c") < 0.0 and R.parse_time("") < 0.0, "parse rejects bad times")

	var soap := R.parse_soap('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
		+ '<u:SetAVTransportURI xmlns:u="urn:schemas-upnp-org:service:AVTransport:1"><InstanceID>0</InstanceID>'
		+ '<CurrentURI>http://phone/v.mp4?a=1&amp;b=2</CurrentURI><CurrentURIMetaData>&lt;DIDL-Lite&gt;&lt;dc:title&gt;A &amp;amp; B&lt;/dc:title&gt;&lt;/DIDL-Lite&gt;</CurrentURIMetaData>'
		+ '</u:SetAVTransportURI></s:Body></s:Envelope>')
	check(soap.get("action") == "SetAVTransportURI", "soap action: %s" % soap)
	check(soap.get("args", {}).get("CurrentURI") == "http://phone/v.mp4?a=1&b=2", "soap uri unescaped once")
	check(soap.get("args", {}).get("CurrentURIMetaData") == "<DIDL-Lite><dc:title>A &amp; B</dc:title></DIDL-Lite>", "soap metadata")
	check(R.didl_title(soap.args.CurrentURIMetaData) == "A & B", "didl title")
	var empty := R.parse_soap('<s:Envelope xmlns:s="x"><s:Body><u:Stop xmlns:u="y"><InstanceID>0</InstanceID><Speed/></u:Stop></s:Body></s:Envelope>')
	check(empty.get("action") == "Stop" and empty.args.get("Speed") == "", "empty argument")
	var bare := R.parse_soap('<s:Envelope xmlns:s="x"><s:Body><u:GetCurrentConnectionIDs xmlns:u="y"/></s:Body></s:Envelope>')
	check(bare.get("action") == "GetCurrentConnectionIDs", "action with no arguments")
	check(R.parse_soap("").is_empty() and R.parse_soap("not xml").is_empty(), "not SOAP")
	check(R.didl_title("") == "" and R.didl_title("<DIDL-Lite><item/></DIDL-Lite>") == "", "no title")

	var plain := R.parse_request("POST /x HTTP/1.1\r\nContent-Length: 3\r\n\r\nabc".to_utf8_buffer())
	check(plain.get("body") == "abc" and plain.get("method") == "POST", "plain request")
	check(R.parse_request("POST /x HTTP/1.1\r\nContent-Length: 5\r\n\r\nabc".to_utf8_buffer()).get("waiting", false), "waits for the body")
	check(R.parse_request("POST /x HTTP/1.1\r\nContent-Length: 5\r\nExpect: 100-continue\r\n\r\n".to_utf8_buffer()).get("expect_continue", false), "Expect: 100-continue")
	var chunked := R.parse_request("POST /x HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n2;x=y\r\nde\r\n0\r\n\r\n".to_utf8_buffer())
	check(chunked.get("body") == "abcde", "chunked body: %s" % chunked)
	check(R.parse_request("POST /x HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nab".to_utf8_buffer()).get("waiting", false), "waits for chunks")
	check(R.parse_request("GET /x HTTP/1.1\r\n".to_utf8_buffer()).is_empty(), "headers incomplete")

	check(R.subscription_seconds("Second-300") == 300 and R.subscription_seconds("infinite") == 1800
		and R.subscription_seconds("Second-99999") == 1800 and R.subscription_seconds("Second-5") == 60, "subscription length")
	var uuid := R.derived_uuid("seed")
	check(uuid == R.derived_uuid("seed") and uuid != R.derived_uuid("other") and uuid.length() == 36 and uuid.count("-") == 4, "derived uuid")

	var ev := R.event_body("AVTransport", {"TransportState": "PLAYING", "AVTransportURIMetaData": "<a x=\"1\">&</a>"})
	var p := XMLParser.new()
	p.open_buffer(ev.to_utf8_buffer())
	var last_change := ""
	var in_lc := false
	while p.read() == OK:
		if p.get_node_type() == XMLParser.NODE_ELEMENT:
			in_lc = p.get_node_name() == "LastChange"
		elif p.get_node_type() == XMLParser.NODE_TEXT and in_lc:
			last_change += p.get_node_data()
	check('<TransportState val="PLAYING"/>' in last_change and 'val="&lt;a x=&quot;1&quot;&gt;&amp;&lt;/a&gt;"' in last_change, "LastChange: %s" % last_change)
	check('channel="Master" val="40"' in R.event_body("RenderingControl", {"Volume": "40"}).xml_unescape(), "volume event")

	check(Ssdp._common_bits("192.168.1.5", "192.168.1.9") == 28 and Ssdp._common_bits("10.0.0.1", "192.168.0.1") == 0, "common bits")
	check(Ssdp.address_for("127.0.0.1") == "127.0.0.1", "loopback peer")
	check(Ssdp.http_date().ends_with(" GMT") and Ssdp.http_date().length() == 29, "http date: " + Ssdp.http_date())

	for short in Upnp.SERVICES:
		var x := XMLParser.new()
		check(x.open_buffer(Upnp.scpd_xml(short).to_utf8_buffer()) == OK, "scpd " + short)
		for action in Upnp.ACTIONS[short]:
			for a in Upnp.ACTIONS[short][action]:
				check(Upnp.VARIABLES[short].has(a[2]), "%s.%s argument %s refers to a missing variable %s" % [short, action, a[0], a[2]])
	check("http-get:*:video/mp4:*" in Upnp.sink_protocol_info(), "sink protocol info")
	check("<friendlyName>A &amp; B</friendlyName>" in Upnp.device_xml("A & B", "uuid:1"), "device name escaped")

	var args := FfmpegConverter.args("http://x/v.mp4", "/tmp/o.ogv")
	check("-protocol_whitelist" in args and "file" not in args[args.find("-protocol_whitelist") + 1], "network-only protocols for links")
	check(not "-protocol_whitelist" in FfmpegConverter.args("/tmp/v.mp4", "/tmp/o.ogv"), "no whitelist for files")
	check(is_equal_approx(FfmpegConverter.parse_duration("  Duration: 00:01:02.50, start"), 62.5), "ffmpeg duration")

	print("DONE %d" % failures)
	quit()
