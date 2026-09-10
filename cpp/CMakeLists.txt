cmake_minimum_required(VERSION 3.16)
project(l2mm_orderbook VERSION 0.1.0 LANGUAGES CXX)

find_package(OpenSSL REQUIRED COMPONENTS Crypto)

add_library(l2mm_book src/orderbook.cpp)
target_include_directories(l2mm_book PUBLIC include)
target_compile_features(l2mm_book PUBLIC cxx_std_17)
set_target_properties(l2mm_book PROPERTIES CXX_EXTENSIONS OFF)
target_link_libraries(l2mm_book PUBLIC OpenSSL::Crypto)

if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(l2mm_book PRIVATE -Wall -Wextra -Wpedantic -Wconversion)
endif()

add_executable(l2mm_orderbook src/main.cpp)
target_link_libraries(l2mm_orderbook PRIVATE l2mm_book)
target_compile_definitions(l2mm_orderbook PRIVATE L2MM_BUILD_TYPE="$<CONFIG>")

include(CTest)
if(BUILD_TESTING)
    add_executable(l2mm_orderbook_tests tests/orderbook_test.cpp)
    target_link_libraries(l2mm_orderbook_tests PRIVATE l2mm_book)
    add_test(NAME orderbook_native COMMAND l2mm_orderbook_tests)
endif()
