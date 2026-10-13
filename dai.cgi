#!/usr/bin/perl

$copyright = <<EOL;
<!--
     Script for amezo-like BBS HEAD LINE
     Copyright (C) 2000 amezo_
     http://www17.big.or.jp/~amezo_/
-->
EOL

#if($ENV{'QUERY_STRING'}){
#	print "Content-type: text/plain; charset=UTF-8\n\n";
#	open(R, '<', "./dai.cgi");
#	print while <R>;
#	close(R);
#	exit;
#}

print "Content-type: text/html; charset=UTF-8\n\n";
print '<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">' . "\n";
print '<meta name="viewport" content="width=device-width, initial-scale=1">' . "\n";

open(B, '<', "./blist.txt") || exit;
while(<B>){
	s/\r?\n\z//;                 # CRLF の blist.txt でも動くように
	next unless length $_;
	($folder, $color, $title) = split(/,/);
	next unless defined $folder && length $folder;
	$color = '' unless defined $color;
	$title = '' unless defined $title;
	$color =~ s/!.*//;           # 背景画像の指定は使わない
	$color =~ s/\*$//;           # 白文字指定の * は色ではない
	$title =~ s/#c#/,/g;         # amezo.cgi と同じく #c# をカンマに戻す
	next if $folder =~ /^\./;
	print <<EOF;
<table border=0 cellspacing=1 cellpadding=2 width=100% bgcolor="$color"><tr><td>
<a href="$folder/" target="naka"><b><font size="3" color="#F92500">$title</font></b></a>
EOF
	if(open(R, '<', "./$folder/index.html")){   # まだ index が無い板でも落ちない
		while(<R>){
			last if /<!-- HEAD LINE -->/;
		}
		for($i=0;$i<20 && ($_ = <R>);$i++){
			last if /<!-- HEAD LINE END -->/;
			s/(\d+\.html)/$folder\/$1/;
			print;
		}
		close(R);
	}
	print "</td></tr></table>\n";
}
close(B);
