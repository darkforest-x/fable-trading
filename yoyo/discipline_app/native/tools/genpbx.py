"""Regenerates SpikeDiscipline.xcodeproj/project.pbxproj deterministically (run from native/).

Synchronized folders compile everything under SpikeDiscipline/ and SpikeDisciplineTests/; the coin
logos are a folder reference; device builds embed this Mac's service token (never committed).
"""
import hashlib
def oid(name): return hashlib.md5(('spike-native:'+name).encode()).hexdigest()[:24].upper()
I={k:oid(k) for k in ['grpApp','grpTests','refApp','refTests','refCoins','bfCoins','fwApp','fwTests','grpRoot','grpProducts','tgtApp','tgtTests','proj','resApp','resTests','srcApp','srcTests','dep','proxy','cfgPD','cfgPR','cfgAD','cfgAR','cfgTD','cfgTR','listP','listA','listT','embed']}
T='\t\t\t\t'
SHELL_TEXT = '# Owner 2026-11-07: no token entry in the app. Device builds carry this Mac\'s existing\n# discipline-service token (never committed); simulator builds never do.\nSRC="$HOME/Library/Application Support/SpikeDiscipline/access-token"\nDST="$TARGET_BUILD_DIR/$UNLOCALIZED_RESOURCES_FOLDER_PATH/service-token"\nrm -f "$DST"\nif [ "$PLATFORM_NAME" = "iphoneos" ] && [ -f "$SRC" ]; then\n  tr -d \' \\r\\n\' < "$SRC" > "$DST"\n  chmod 600 "$DST"\nfi\n'
SCRIPT = SHELL_TEXT.replace('\\','\\\\').replace('"','\\"').replace('\n','\\n')
def lines(*xs): return ''.join(T+x+'\n' for x in xs)
common=lines('ALWAYS_SEARCH_USER_PATHS = NO;','CLANG_ENABLE_MODULES = YES;','CLANG_ENABLE_OBJC_ARC = YES;','ENABLE_STRICT_OBJC_MSGSEND = YES;','GCC_C_LANGUAGE_STANDARD = gnu17;','IPHONEOS_DEPLOYMENT_TARGET = 26.0;','LOCALIZATION_PREFERS_STRING_CATALOGS = YES;','SDKROOT = iphoneos;','SWIFT_VERSION = 5.0;','TARGETED_DEVICE_FAMILY = 1;')
app=lines('ASSETCATALOG_COMPILER_APPICON_NAME = AppIcon;','ASSETCATALOG_COMPILER_GLOBAL_ACCENT_COLOR_NAME = AccentColor;','CODE_SIGN_STYLE = Automatic;','CURRENT_PROJECT_VERSION = 11;','DEVELOPMENT_TEAM = L7RP7HZT53;','ENABLE_PREVIEWS = YES;','ENABLE_USER_SCRIPT_SANDBOXING = NO;','GENERATE_INFOPLIST_FILE = YES;','INFOPLIST_KEY_CFBundleDisplayName = "天才交易员";','INFOPLIST_KEY_LSApplicationCategoryType = "public.app-category.finance";','INFOPLIST_KEY_UIApplicationSceneManifest_Generation = YES;','INFOPLIST_KEY_UILaunchScreen_Generation = YES;','INFOPLIST_KEY_UISupportedInterfaceOrientations = UIInterfaceOrientationPortrait;','LD_RUNPATH_SEARCH_PATHS = "$(inherited) @executable_path/Frameworks";','MARKETING_VERSION = 2.5.0;','PRODUCT_BUNDLE_IDENTIFIER = com.spike.discipline.native;','PRODUCT_NAME = "$(TARGET_NAME)";','SWIFT_EMIT_LOC_STRINGS = YES;')
appD=app+lines('SWIFT_ACTIVE_COMPILATION_CONDITIONS = "DEBUG $(inherited)";','SWIFT_OPTIMIZATION_LEVEL = "-Onone";')
appR=app+lines('SWIFT_OPTIMIZATION_LEVEL = "-O";')
test=lines('BUNDLE_LOADER = "$(TEST_HOST)";','CODE_SIGN_STYLE = Automatic;','DEVELOPMENT_TEAM = L7RP7HZT53;','GENERATE_INFOPLIST_FILE = YES;','PRODUCT_BUNDLE_IDENTIFIER = com.spike.discipline.native.tests;','PRODUCT_NAME = "$(TARGET_NAME)";','TEST_HOST = "$(BUILT_PRODUCTS_DIR)/SpikeDiscipline.app/$(BUNDLE_EXECUTABLE_FOLDER_PATH)/SpikeDiscipline";')
projD=common+lines('DEBUG_INFORMATION_FORMAT = dwarf;','ENABLE_TESTABILITY = YES;','ONLY_ACTIVE_ARCH = YES;')
projR=common+lines('DEBUG_INFORMATION_FORMAT = "dwarf-with-dsym";','VALIDATE_PRODUCT = YES;')
def cfg(key,name,body): return '\t\t%s /* %s */ = {\n\t\t\tisa = XCBuildConfiguration;\n\t\t\tbuildSettings = {\n%s\t\t\t};\n\t\t\tname = %s;\n\t\t};\n'%(I[key],name,body,name)
o=[]
o.append('// !$*UTF8*$!\n{\n\tarchiveVersion = 1;\n\tclasses = {\n\t};\n\tobjectVersion = 77;\n\tobjects = {\n\n')
o.append('\t\t%s /* coins in Resources */ = {isa = PBXBuildFile; fileRef = %s /* coins */; };\n'%(I['bfCoins'],I['refCoins']))
o.append('\t\t%s = {isa = PBXContainerItemProxy; containerPortal = %s; proxyType = 1; remoteGlobalIDString = %s; remoteInfo = SpikeDiscipline; };\n'%(I['proxy'],I['proj'],I['tgtApp']))
o.append('\t\t%s /* SpikeDiscipline.app */ = {isa = PBXFileReference; explicitFileType = wrapper.application; includeInIndex = 0; path = SpikeDiscipline.app; sourceTree = BUILT_PRODUCTS_DIR; };\n'%I['refApp'])
o.append('\t\t%s /* SpikeDisciplineTests.xctest */ = {isa = PBXFileReference; explicitFileType = wrapper.cfbundle; includeInIndex = 0; path = SpikeDisciplineTests.xctest; sourceTree = BUILT_PRODUCTS_DIR; };\n'%I['refTests'])
o.append('\t\t%s /* coins */ = {isa = PBXFileReference; lastKnownFileType = folder; name = coins; path = ../mobile/src/assets/coins; sourceTree = SOURCE_ROOT; };\n'%I['refCoins'])
o.append('\t\t%s /* SpikeDiscipline */ = {isa = PBXFileSystemSynchronizedRootGroup; path = SpikeDiscipline; sourceTree = "<group>"; };\n'%I['grpApp'])
o.append('\t\t%s /* SpikeDisciplineTests */ = {isa = PBXFileSystemSynchronizedRootGroup; path = SpikeDisciplineTests; sourceTree = "<group>"; };\n'%I['grpTests'])
for k in ('fwApp','fwTests'): o.append('\t\t%s /* Frameworks */ = {isa = PBXFrameworksBuildPhase; buildActionMask = 2147483647; files = (); runOnlyForDeploymentPostprocessing = 0; };\n'%I[k])
o.append('\t\t%s = {isa = PBXGroup; children = (%s, %s, %s, %s); sourceTree = "<group>"; };\n'%(I['grpRoot'],I['grpApp'],I['grpTests'],I['refCoins'],I['grpProducts']))
o.append('\t\t%s /* Products */ = {isa = PBXGroup; children = (%s, %s); name = Products; sourceTree = "<group>"; };\n'%(I['grpProducts'],I['refApp'],I['refTests']))
o.append('\t\t%s /* SpikeDiscipline */ = {isa = PBXNativeTarget; buildConfigurationList = %s; buildPhases = (%s, %s, %s, %s); buildRules = (); dependencies = (); fileSystemSynchronizedGroups = (%s); name = SpikeDiscipline; packageProductDependencies = (); productName = SpikeDiscipline; productReference = %s; productType = "com.apple.product-type.application"; };\n'%(I['tgtApp'],I['listA'],I['srcApp'],I['fwApp'],I['resApp'],I['embed'],I['grpApp'],I['refApp']))
o.append('\t\t%s /* SpikeDisciplineTests */ = {isa = PBXNativeTarget; buildConfigurationList = %s; buildPhases = (%s, %s, %s); buildRules = (); dependencies = (%s); fileSystemSynchronizedGroups = (%s); name = SpikeDisciplineTests; packageProductDependencies = (); productName = SpikeDisciplineTests; productReference = %s; productType = "com.apple.product-type.bundle.unit-test"; };\n'%(I['tgtTests'],I['listT'],I['srcTests'],I['fwTests'],I['resTests'],I['dep'],I['grpTests'],I['refTests']))
o.append('\t\t%s /* Project object */ = {isa = PBXProject; attributes = {BuildIndependentTargetsInParallel = 1; LastSwiftUpdateCheck = 2600; LastUpgradeCheck = 2600; TargetAttributes = {%s = {CreatedOnToolsVersion = 26.0; }; %s = {CreatedOnToolsVersion = 26.0; TestTargetID = %s; }; }; }; buildConfigurationList = %s; developmentRegion = "zh-Hans"; hasScannedForEncodings = 0; knownRegions = (en, Base, "zh-Hans"); mainGroup = %s; minimizedProjectReferenceProxies = 1; preferredProjectObjectVersion = 77; productRefGroup = %s; projectDirPath = ""; projectRoot = ""; targets = (%s, %s); };\n'%(I['proj'],I['tgtApp'],I['tgtTests'],I['tgtApp'],I['listP'],I['grpRoot'],I['grpProducts'],I['tgtApp'],I['tgtTests']))
o.append('\t\t%s /* Resources */ = {isa = PBXResourcesBuildPhase; buildActionMask = 2147483647; files = (%s); runOnlyForDeploymentPostprocessing = 0; };\n'%(I['resApp'],I['bfCoins']))
o.append('\t\t%s /* Resources */ = {isa = PBXResourcesBuildPhase; buildActionMask = 2147483647; files = (); runOnlyForDeploymentPostprocessing = 0; };\n'%I['resTests'])
for k in ('srcApp','srcTests'): o.append('\t\t%s /* Sources */ = {isa = PBXSourcesBuildPhase; buildActionMask = 2147483647; files = (); runOnlyForDeploymentPostprocessing = 0; };\n'%I[k])
o.append('\t\t%s /* Embed service token */ = {isa = PBXShellScriptBuildPhase; alwaysOutOfDate = 1; buildActionMask = 2147483647; files = (); inputFileListPaths = (); inputPaths = (); name = "Embed service token"; outputFileListPaths = (); outputPaths = (); runOnlyForDeploymentPostprocessing = 0; shellPath = /bin/sh; shellScript = "%s"; showEnvVarsInLog = 0; };\n'%(I['embed'],SCRIPT))
o.append('\t\t%s = {isa = PBXTargetDependency; target = %s; targetProxy = %s; };\n'%(I['dep'],I['tgtApp'],I['proxy']))
o.append(cfg('cfgPD','Debug',projD)+cfg('cfgPR','Release',projR)+cfg('cfgAD','Debug',appD)+cfg('cfgAR','Release',appR)+cfg('cfgTD','Debug',test)+cfg('cfgTR','Release',test))
for k,a,b in (('listP','cfgPD','cfgPR'),('listA','cfgAD','cfgAR'),('listT','cfgTD','cfgTR')):
    o.append('\t\t%s = {isa = XCConfigurationList; buildConfigurations = (%s, %s); defaultConfigurationIsVisible = 0; defaultConfigurationName = Release; };\n'%(I[k],I[a],I[b]))
o.append('\t};\n\trootObject = %s /* Project object */;\n}\n'%I['proj'])
open('SpikeDiscipline.xcodeproj/project.pbxproj','w').write(''.join(o))
print('ok')
