"""Regenerates SpikeDiscipline.xcodeproj/project.pbxproj deterministically (run from native/).

Synchronized folders compile everything under SpikeDiscipline/ (app), SpikeWidgets/ (widget + Live
Activity extension, 2.6.0) and SpikeShared/ (compiled into both: activity attributes, widget
snapshot), and SpikeDisciplineTests/. The coin logos are a folder reference; device builds embed
this Mac's service token (never committed) into the app only.
"""
import hashlib
def oid(name): return hashlib.md5(('spike-native:'+name).encode()).hexdigest()[:24].upper()
I={k:oid(k) for k in ['grpApp','grpTests','refApp','refTests','refCoins','bfCoins','fwApp','fwTests','grpRoot','grpProducts','tgtApp','tgtTests','proj','resApp','resTests','srcApp','srcTests','dep','proxy','cfgPD','cfgPR','cfgAD','cfgAR','cfgTD','cfgTR','listP','listA','listT','embed',
                      'grpWidgets','grpShared','refWidget','tgtWidget','srcWidget','fwWidget','resWidget','cfgWD','cfgWR','listW','depWidget','proxyWidget','bfWidget','copyExt']}
T='\t\t\t\t'
VERSION, BUILD = '2.6.2', '14'
# Native APNs push needs a paid Apple Developer Program team: the current signing team is a personal
# team, which cannot provision Push Notifications (device build 2026-11-08). Off = no aps-environment
# entitlement and no APNS compile flag; the Live Activity is then updated by the app only.
PUSH = False
ENTITLEMENTS = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
%s	<key>com.apple.security.application-groups</key>
	<array>
		<string>group.com.spike.discipline.native</string>
	</array>
</dict>
</plist>
"""
open('SpikeDiscipline.entitlements', 'w').write(ENTITLEMENTS % ('\t<key>aps-environment</key>\n\t<string>development</string>\n' if PUSH else ''))
FLAGS = 'DEBUG APNS $(inherited)' if PUSH else 'DEBUG $(inherited)'
RFLAGS = 'APNS $(inherited)' if PUSH else '$(inherited)'
SHELL_TEXT = '# Owner 2026-11-07: no token entry in the app. Device builds carry this Mac\'s existing\n# discipline-service token (never committed); simulator builds never do.\nSRC="$HOME/Library/Application Support/SpikeDiscipline/access-token"\nDST="$TARGET_BUILD_DIR/$UNLOCALIZED_RESOURCES_FOLDER_PATH/service-token"\nrm -f "$DST"\nif [ "$PLATFORM_NAME" = "iphoneos" ] && [ -f "$SRC" ]; then\n  tr -d \' \\r\\n\' < "$SRC" > "$DST"\n  chmod 600 "$DST"\nfi\n'
SCRIPT = SHELL_TEXT.replace('\\','\\\\').replace('"','\\"').replace('\n','\\n')
def lines(*xs): return ''.join(T+x+'\n' for x in xs)
common=lines('ALWAYS_SEARCH_USER_PATHS = NO;','CLANG_ENABLE_MODULES = YES;','CLANG_ENABLE_OBJC_ARC = YES;','ENABLE_STRICT_OBJC_MSGSEND = YES;','GCC_C_LANGUAGE_STANDARD = gnu17;','IPHONEOS_DEPLOYMENT_TARGET = 26.0;','LOCALIZATION_PREFERS_STRING_CATALOGS = YES;','SDKROOT = iphoneos;','SWIFT_VERSION = 5.0;','TARGETED_DEVICE_FAMILY = 1;')
app=lines('ASSETCATALOG_COMPILER_APPICON_NAME = AppIcon;','ASSETCATALOG_COMPILER_GLOBAL_ACCENT_COLOR_NAME = AccentColor;','CODE_SIGN_ENTITLEMENTS = SpikeDiscipline.entitlements;','CODE_SIGN_STYLE = Automatic;',f'CURRENT_PROJECT_VERSION = {BUILD};','DEVELOPMENT_TEAM = L7RP7HZT53;','ENABLE_PREVIEWS = YES;','ENABLE_USER_SCRIPT_SANDBOXING = NO;','GENERATE_INFOPLIST_FILE = YES;','INFOPLIST_KEY_CFBundleDisplayName = "天才交易员";','INFOPLIST_KEY_LSApplicationCategoryType = "public.app-category.finance";','INFOPLIST_KEY_NSSupportsLiveActivities = YES;','INFOPLIST_KEY_NSSupportsLiveActivitiesFrequentUpdates = YES;','INFOPLIST_KEY_UIApplicationSceneManifest_Generation = YES;','INFOPLIST_KEY_UILaunchScreen_Generation = YES;','INFOPLIST_KEY_UISupportedInterfaceOrientations = "UIInterfaceOrientationPortrait UIInterfaceOrientationLandscapeLeft UIInterfaceOrientationLandscapeRight";','LD_RUNPATH_SEARCH_PATHS = "$(inherited) @executable_path/Frameworks";',f'MARKETING_VERSION = {VERSION};','PRODUCT_BUNDLE_IDENTIFIER = com.spike.discipline.native;','PRODUCT_NAME = "$(TARGET_NAME)";','SWIFT_EMIT_LOC_STRINGS = YES;')
appD=app+lines(f'SWIFT_ACTIVE_COMPILATION_CONDITIONS = "{FLAGS}";','SWIFT_OPTIMIZATION_LEVEL = "-Onone";')
appR=app+lines(f'SWIFT_ACTIVE_COMPILATION_CONDITIONS = "{RFLAGS}";','SWIFT_OPTIMIZATION_LEVEL = "-O";')
widget=lines('APPLICATION_EXTENSION_API_ONLY = YES;','CODE_SIGN_ENTITLEMENTS = SpikeWidgets.entitlements;','CODE_SIGN_STYLE = Automatic;',f'CURRENT_PROJECT_VERSION = {BUILD};','DEVELOPMENT_TEAM = L7RP7HZT53;','GENERATE_INFOPLIST_FILE = YES;','INFOPLIST_FILE = "SpikeWidgets-Info.plist";','INFOPLIST_KEY_CFBundleDisplayName = "天才交易员";','LD_RUNPATH_SEARCH_PATHS = "$(inherited) @executable_path/Frameworks @executable_path/../../Frameworks";',f'MARKETING_VERSION = {VERSION};','PRODUCT_BUNDLE_IDENTIFIER = com.spike.discipline.native.widgets;','PRODUCT_NAME = "$(TARGET_NAME)";','SKIP_INSTALL = YES;','SWIFT_EMIT_LOC_STRINGS = YES;')
widgetD=widget+lines('SWIFT_ACTIVE_COMPILATION_CONDITIONS = "DEBUG $(inherited)";','SWIFT_OPTIMIZATION_LEVEL = "-Onone";')
widgetR=widget+lines('SWIFT_OPTIMIZATION_LEVEL = "-O";')
test=lines('BUNDLE_LOADER = "$(TEST_HOST)";','CODE_SIGN_STYLE = Automatic;','DEVELOPMENT_TEAM = L7RP7HZT53;','GENERATE_INFOPLIST_FILE = YES;','PRODUCT_BUNDLE_IDENTIFIER = com.spike.discipline.native.tests;','PRODUCT_NAME = "$(TARGET_NAME)";','TEST_HOST = "$(BUILT_PRODUCTS_DIR)/SpikeDiscipline.app/$(BUNDLE_EXECUTABLE_FOLDER_PATH)/SpikeDiscipline";')
projD=common+lines('DEBUG_INFORMATION_FORMAT = dwarf;','ENABLE_TESTABILITY = YES;','ONLY_ACTIVE_ARCH = YES;')
projR=common+lines('DEBUG_INFORMATION_FORMAT = "dwarf-with-dsym";','VALIDATE_PRODUCT = YES;')
def cfg(key,name,body): return '\t\t%s /* %s */ = {\n\t\t\tisa = XCBuildConfiguration;\n\t\t\tbuildSettings = {\n%s\t\t\t};\n\t\t\tname = %s;\n\t\t};\n'%(I[key],name,body,name)
o=[]
o.append('// !$*UTF8*$!\n{\n\tarchiveVersion = 1;\n\tclasses = {\n\t};\n\tobjectVersion = 77;\n\tobjects = {\n\n')
o.append('\t\t%s /* coins in Resources */ = {isa = PBXBuildFile; fileRef = %s /* coins */; };\n'%(I['bfCoins'],I['refCoins']))
o.append('\t\t%s /* SpikeWidgets.appex in Embed Foundation Extensions */ = {isa = PBXBuildFile; fileRef = %s /* SpikeWidgets.appex */; settings = {ATTRIBUTES = (RemoveHeadersOnCopy, ); }; };\n'%(I['bfWidget'],I['refWidget']))
o.append('\t\t%s = {isa = PBXContainerItemProxy; containerPortal = %s; proxyType = 1; remoteGlobalIDString = %s; remoteInfo = SpikeDiscipline; };\n'%(I['proxy'],I['proj'],I['tgtApp']))
o.append('\t\t%s = {isa = PBXContainerItemProxy; containerPortal = %s; proxyType = 1; remoteGlobalIDString = %s; remoteInfo = SpikeWidgets; };\n'%(I['proxyWidget'],I['proj'],I['tgtWidget']))
o.append('\t\t%s /* Embed Foundation Extensions */ = {isa = PBXCopyFilesBuildPhase; buildActionMask = 2147483647; dstPath = ""; dstSubfolderSpec = 13; files = (%s); name = "Embed Foundation Extensions"; runOnlyForDeploymentPostprocessing = 0; };\n'%(I['copyExt'],I['bfWidget']))
o.append('\t\t%s /* SpikeDiscipline.app */ = {isa = PBXFileReference; explicitFileType = wrapper.application; includeInIndex = 0; path = SpikeDiscipline.app; sourceTree = BUILT_PRODUCTS_DIR; };\n'%I['refApp'])
o.append('\t\t%s /* SpikeDisciplineTests.xctest */ = {isa = PBXFileReference; explicitFileType = wrapper.cfbundle; includeInIndex = 0; path = SpikeDisciplineTests.xctest; sourceTree = BUILT_PRODUCTS_DIR; };\n'%I['refTests'])
o.append('\t\t%s /* SpikeWidgets.appex */ = {isa = PBXFileReference; explicitFileType = "wrapper.app-extension"; includeInIndex = 0; path = SpikeWidgets.appex; sourceTree = BUILT_PRODUCTS_DIR; };\n'%I['refWidget'])
o.append('\t\t%s /* coins */ = {isa = PBXFileReference; lastKnownFileType = folder; name = coins; path = ../mobile/src/assets/coins; sourceTree = SOURCE_ROOT; };\n'%I['refCoins'])
for key, path in (('grpApp','SpikeDiscipline'),('grpTests','SpikeDisciplineTests'),('grpWidgets','SpikeWidgets'),('grpShared','SpikeShared')):
    o.append('\t\t%s /* %s */ = {isa = PBXFileSystemSynchronizedRootGroup; path = %s; sourceTree = "<group>"; };\n'%(I[key],path,path))
for k in ('fwApp','fwTests','fwWidget'): o.append('\t\t%s /* Frameworks */ = {isa = PBXFrameworksBuildPhase; buildActionMask = 2147483647; files = (); runOnlyForDeploymentPostprocessing = 0; };\n'%I[k])
o.append('\t\t%s = {isa = PBXGroup; children = (%s, %s, %s, %s, %s, %s); sourceTree = "<group>"; };\n'%(I['grpRoot'],I['grpApp'],I['grpShared'],I['grpWidgets'],I['grpTests'],I['refCoins'],I['grpProducts']))
o.append('\t\t%s /* Products */ = {isa = PBXGroup; children = (%s, %s, %s); name = Products; sourceTree = "<group>"; };\n'%(I['grpProducts'],I['refApp'],I['refTests'],I['refWidget']))
o.append('\t\t%s /* SpikeDiscipline */ = {isa = PBXNativeTarget; buildConfigurationList = %s; buildPhases = (%s, %s, %s, %s, %s); buildRules = (); dependencies = (%s); fileSystemSynchronizedGroups = (%s, %s); name = SpikeDiscipline; packageProductDependencies = (); productName = SpikeDiscipline; productReference = %s; productType = "com.apple.product-type.application"; };\n'%(I['tgtApp'],I['listA'],I['srcApp'],I['fwApp'],I['resApp'],I['embed'],I['copyExt'],I['depWidget'],I['grpApp'],I['grpShared'],I['refApp']))
o.append('\t\t%s /* SpikeWidgets */ = {isa = PBXNativeTarget; buildConfigurationList = %s; buildPhases = (%s, %s, %s); buildRules = (); dependencies = (); fileSystemSynchronizedGroups = (%s, %s); name = SpikeWidgets; packageProductDependencies = (); productName = SpikeWidgets; productReference = %s; productType = "com.apple.product-type.app-extension"; };\n'%(I['tgtWidget'],I['listW'],I['srcWidget'],I['fwWidget'],I['resWidget'],I['grpWidgets'],I['grpShared'],I['refWidget']))
o.append('\t\t%s /* SpikeDisciplineTests */ = {isa = PBXNativeTarget; buildConfigurationList = %s; buildPhases = (%s, %s, %s); buildRules = (); dependencies = (%s); fileSystemSynchronizedGroups = (%s); name = SpikeDisciplineTests; packageProductDependencies = (); productName = SpikeDisciplineTests; productReference = %s; productType = "com.apple.product-type.bundle.unit-test"; };\n'%(I['tgtTests'],I['listT'],I['srcTests'],I['fwTests'],I['resTests'],I['dep'],I['grpTests'],I['refTests']))
o.append('\t\t%s /* Project object */ = {isa = PBXProject; attributes = {BuildIndependentTargetsInParallel = 1; LastSwiftUpdateCheck = 2600; LastUpgradeCheck = 2600; TargetAttributes = {%s = {CreatedOnToolsVersion = 26.0; }; %s = {CreatedOnToolsVersion = 26.0; }; %s = {CreatedOnToolsVersion = 26.0; TestTargetID = %s; }; }; }; buildConfigurationList = %s; developmentRegion = "zh-Hans"; hasScannedForEncodings = 0; knownRegions = (en, Base, "zh-Hans"); mainGroup = %s; minimizedProjectReferenceProxies = 1; preferredProjectObjectVersion = 77; productRefGroup = %s; projectDirPath = ""; projectRoot = ""; targets = (%s, %s, %s); };\n'%(I['proj'],I['tgtApp'],I['tgtWidget'],I['tgtTests'],I['tgtApp'],I['listP'],I['grpRoot'],I['grpProducts'],I['tgtApp'],I['tgtWidget'],I['tgtTests']))
o.append('\t\t%s /* Resources */ = {isa = PBXResourcesBuildPhase; buildActionMask = 2147483647; files = (%s); runOnlyForDeploymentPostprocessing = 0; };\n'%(I['resApp'],I['bfCoins']))
for k in ('resTests','resWidget'): o.append('\t\t%s /* Resources */ = {isa = PBXResourcesBuildPhase; buildActionMask = 2147483647; files = (); runOnlyForDeploymentPostprocessing = 0; };\n'%I[k])
for k in ('srcApp','srcTests','srcWidget'): o.append('\t\t%s /* Sources */ = {isa = PBXSourcesBuildPhase; buildActionMask = 2147483647; files = (); runOnlyForDeploymentPostprocessing = 0; };\n'%I[k])
o.append('\t\t%s /* Embed service token */ = {isa = PBXShellScriptBuildPhase; alwaysOutOfDate = 1; buildActionMask = 2147483647; files = (); inputFileListPaths = (); inputPaths = (); name = "Embed service token"; outputFileListPaths = (); outputPaths = (); runOnlyForDeploymentPostprocessing = 0; shellPath = /bin/sh; shellScript = "%s"; showEnvVarsInLog = 0; };\n'%(I['embed'],SCRIPT))
o.append('\t\t%s = {isa = PBXTargetDependency; target = %s; targetProxy = %s; };\n'%(I['dep'],I['tgtApp'],I['proxy']))
o.append('\t\t%s = {isa = PBXTargetDependency; target = %s; targetProxy = %s; };\n'%(I['depWidget'],I['tgtWidget'],I['proxyWidget']))
o.append(cfg('cfgPD','Debug',projD)+cfg('cfgPR','Release',projR)+cfg('cfgAD','Debug',appD)+cfg('cfgAR','Release',appR)+cfg('cfgWD','Debug',widgetD)+cfg('cfgWR','Release',widgetR)+cfg('cfgTD','Debug',test)+cfg('cfgTR','Release',test))
for k,a,b in (('listP','cfgPD','cfgPR'),('listA','cfgAD','cfgAR'),('listW','cfgWD','cfgWR'),('listT','cfgTD','cfgTR')):
    o.append('\t\t%s = {isa = XCConfigurationList; buildConfigurations = (%s, %s); defaultConfigurationIsVisible = 0; defaultConfigurationName = Release; };\n'%(I[k],I[a],I[b]))
o.append('\t};\n\trootObject = %s /* Project object */;\n}\n'%I['proj'])
open('SpikeDiscipline.xcodeproj/project.pbxproj','w').write(''.join(o))
print('ok')
